from django.utils import timezone
from django.conf import settings
from history.st_api import jobs_api_call, appointment_assignments_api_call, customers_api_call, locations_api_call, estimates_api_call
from history.models import DispatchJob
from streaming.models import UserProfile
from django_q.models import Task
from datetime import timedelta
from history.push_notifications import send_tech_status_push
from history.st_api import invoices_api_call
from chunking.s3_handler_hybrid import get_s3_client, generate_presigned_download_url
import json
import tiktoken

def pollA():
    try:
        print("pollA...")
        dispatch_jobs = DispatchJob.objects.filter(active=True)
        for d_job in dispatch_jobs:
            # Get user for push notifications
            try:
                user_profile = UserProfile.objects.get(st_id=d_job.tech_id, active=True)
                user = user_profile.user
            except UserProfile.DoesNotExist:
                user = None

            jobs = jobs_api_call(ids=d_job.job_id)
            if len(jobs) == 0 or ("jobStatus" in jobs[0] and jobs[0]["jobStatus"] in ["Canceled", "Hold","Completed"]):
                # Job was deleted
                if d_job.notified_working and not d_job.notified_done:
                    #
                    # Recording was started and not stopped before job Deleted, Canceled, Held, or Completed:
                    send_tech_status_push(user, 2, appointment_id=d_job.appointment_id)
                    continue
                # Recording not started; close out.
                d_job.polling_active = False
                d_job.notified_history = False
                d_job.notified_done = False
                d_job.notified_working = False
                d_job.active = False
                d_job.save()
                continue
            appointment_assignments = appointment_assignments_api_call(appointmentIds=d_job.appointment_id)
            if len(appointment_assignments) == 0 or appointment_assignments[0]['status'] in ["Done","Scheduled"]:
                # Appointment canceled, marked done, or rescheduled before working
                if d_job.notified_working and not d_job.notified_done:
                    # Recording was started and not stopped before Appointment Canceled:
                    send_tech_status_push(user, 2, appointment_id=d_job.appointment_id)
                    continue
                # Recording not started; close out.
                d_job.polling_active = False
                d_job.notified_history = False
                d_job.notified_done = False
                d_job.notified_working = False
                d_job.active = False
                d_job.save()
                continue
            #
            #   Find tech's data in list, if it is there
            #
            techusers = [str(o.st_id) for o in UserProfile.objects.filter(active=True)]

            for assignment in appointment_assignments:
                if str(assignment["technicianId"]) not in techusers:
                    # This would be a ride-along or helper
                    continue
                #
                #   In the case of multiple techs, we need to be working with the right assignment for the d_job tech_id
                #
                if str(assignment["technicianId"]) != d_job.tech_id:
                    continue

                if assignment["status"] == "Dispatched":
                    #
                    #   Ensure ST polling for "Working" starts
                    #
                    d_job.polling_active = True
                    d_job.save()

                    # Check if history is ready and send push if not already notified
                    if not d_job.notified_history and user and d_job.ai_document_built:
                        send_tech_status_push(user, 3, appointment_id=d_job.appointment_id)
                        d_job.save()
                        print(f"Sent history ready push (result:3) for job {d_job.job_id}")

                elif assignment["status"] == "Working":
                    print(f"Setting DispatchJob status to 'Working' for job {d_job.job_id}")
                    d_job.status = "Working"  # THIS IS WHAT TRIGGERS RECORDING START; don't set active to False
                    #d_job.polling_active = True  # Should already be True; iOS polling will set to False when recording starts
                    # Send push notification if not already sent
                    if not d_job.notified_working and user:

                        send_tech_status_push(user, 1, appointment_id=d_job.appointment_id)
                        #print(f"Sent working push (result:1) for job {d_job.job_id}")

                    d_job.save()
                else:
                    print(f"Assignment error!  status = {assignment['status']}!!")
    except Exception as e:
        print(f"PollA failed: {e}")



def build_ai_job_document(dispatch_job_id, customer_id, location_id):
    """
    Build comprehensive AI job document and upload to S3
    Called as background task when DispatchJob is created
    """
    try:
        dispatch_job = DispatchJob.objects.get(id=dispatch_job_id)
        #job = jobs_api_call(ids=dispatch_job.job_id)[0]
        #customer_id = job["customerId"]

        print(f"Building AI document for job {dispatch_job.job_id}...")

        # Construct the document with these
        document_content = construct_job_document(
            customer_id,
            location_id,
            dispatch_job.job_id,
            dispatch_job.appointment_id,
            dispatch_job.tech_id
        )

        # Upload to S3 and get the S3 key
        s3_key = upload_document_to_s3(
            document_content,
            dispatch_job.job_id,
            dispatch_job.appointment_id
        )

        # Store the S3 key (not URL - we'll generate presigned URLs when needed)
        dispatch_job.ai_document_s3_key = s3_key
        dispatch_job.ai_document_built = True
        dispatch_job.save()

        print(f"✅ AI document built and uploaded: {s3_key}")

        # Send push notification to user that document is ready
        try:
            user_profile = UserProfile.objects.get(st_id=dispatch_job.tech_id)
            user = user_profile.user

            # Send notification - reusing result:3 (or we can create result:4 for AI doc ready)
            send_tech_status_push(user, 3, appointment_id=dispatch_job.appointment_id)
            dispatch_job.notified_history = True  # Reusing this flag
            dispatch_job.save()
            print(f"📱 Sent AI document ready notification (result:3) for job {dispatch_job.job_id} appointment {dispatch_job.appointment_id}")
        except UserProfile.DoesNotExist:
            print(f"⚠️ No user profile found for tech_id {dispatch_job.tech_id}")



    except DispatchJob.DoesNotExist:
        print(f"❌ DispatchJob {dispatch_job_id} not found")
    except Exception as e:
        print(f"❌ Error building AI document: {e}")


def construct_job_document(customer_id, location_id, job_id=None, appointment_id=None, tech_id=None):
    """
    Construct comprehensive job data document from ServiceTitan API
    Easy to expand with additional data sources
    """
    myjson = {}
    myjson = get_customer_info(customer_id, location_id, myjson)
    myjson = get_invoices(customer_id, location_id, myjson)
    myjson = get_estimates(location_id, myjson)

    mydoc = json.dumps(myjson)
    enc = tiktoken.encoding_for_model("gpt-4-turbo")
    tokens = len(enc.encode(str(mydoc)))
    print(f"Tokens: {tokens}")
    return json.dumps(myjson)


def upload_document_to_s3(document_content, job_id, appointment_id):
    """
    Upload job document to S3 (private) and return S3 key
    """
    s3_client = get_s3_client()

    # Create S3 key (path in bucket)
    s3_key = f"ai_documents/job_{job_id}_appt_{appointment_id}.txt"

    # Upload to S3 as private object
    s3_client.put_object(
        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
        Key=s3_key,
        Body=document_content.encode('utf-8'),
        ContentType='text/plain'
    )

    return s3_key

def get_invoices(customer_id, location_id, myjson):
    try:
        print(f"Getting invoices for customer {customer_id}")
        one_year_ago = timezone.now() - timedelta(days=int(settings.HISTORY_MONTHS) * 30)
        formatted_date = one_year_ago.strftime("%Y-%m-%d")
        invoices = invoices_api_call(invoicedOnOrAfter=formatted_date, customerId=customer_id)
        myjson['invoices'] = []
        for invoice in invoices:
            if invoice["job"] is None:
                continue
            if "location" not in invoice:
                continue
            try:
                if str(invoice["location"]["id"]) != str(location_id):
                    print(f"API Invoice location ID {str(invoice['location']['id'])} != {str(location_id)}")
                    continue
            except:
                pass
            inv = {}
            inv["date"] = invoice['invoiceDate']
            inv['work_done'] = invoice['summary']
            inv['total'] = invoice['total']
            inv['line_items'] = []
            for item in invoice['items']:
                if 'generalLedgerAccount' in item:
                    if 'detailType' in item['generalLedgerAccount']:
                        if item['generalLedgerAccount']['detailType'] == 'Income':
                            inv['line_items'].append(
                                item['displayName']
                            )
            myjson['invoices'].append(inv)
    except Exception as e:
        print(f"Error fetching invoices: {e}")
    return myjson

def get_customer_info(customer_id, location_id, myjson):
    try:
        customer_name, address = "Unknown", "Unknown"
        customers = customers_api_call(ids=customer_id)
        if len(customers) > 0:
            customer = customers[0]
            if "name" in customer:
                customer_name = customer["name"]
            if "address" in customer:
                c = customer["address"]
                unit = c["unit"]
                unit = "" if unit is None else f"unit {unit}"
                address = f"{c['street']} {unit}\n{c['city']} {c['state']} {c['zip']}"
        myjson["billing_name"] = customer_name
        myjson["billing_address"] = address
        location_name, address = "Unknown", "Unknown"
        locations = locations_api_call(ids=location_id)
        if len(locations)>0:
            location = locations[0]
            if "name" in location:
                location_name = location["name"]
            if "address" in location:
                l = location["address"]
                unit = l["unit"]
                unit = "" if unit is None else f"unit {unit}"
                address = f"{l['street']} {unit}\n{l['city']} {l['state']} {l['zip']}"
        myjson["location_name"] = location_name
        myjson["location_address"] = address
        myjson["customer_summary"] = (
            f"The job is located at {address} under the name {location_name}, "
            f"and is owned by {customer_name} at {myjson['billing_address']}. "
            "In many cases, the owner and occupant are the same person."
        )
    except Exception as e:
        print(f"Error fetching customer data: {e}")
    return myjson

def get_estimates(location_id, myjson):
    try:
        one_year_ago = timezone.now() - timedelta(days=int(settings.HISTORY_MONTHS) * 30)
        formatted_date = one_year_ago.strftime("%Y-%m-%d")
        estimates = estimates_api_call(locationId=location_id, createdOnOrAfter=formatted_date)
        myjson["estimates"] = []
        for estimate in estimates:
            if estimate["active"] and estimate["status"]["name"] != "dismissed":
                est = {}
                est["name"] = estimate["name"]
                est["summary"] = estimate["summary"][:150]
                est["sold"] = estimate["soldOn"] is not None
                est["total"] = estimate["subtotal"]
                myjson["estimates"].append(est)

    except Exception as e:
        print(f"Error fetching estimates: {e}")
    return myjson
