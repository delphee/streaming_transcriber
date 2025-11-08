from django.utils import timezone
from django.conf import settings
from history.st_api import jobs_api_call, appointment_assignments_api_call
from history.models import DispatchJob, HistoryJob
from streaming.models import UserProfile
from django_q.models import Task
from datetime import timedelta
from history.push_notifications import send_tech_status_push
from history.st_api import invoices_api_call
import boto3
from chunking.s3_handler import get_s3_client, generate_presigned_download_url

def pollA():
    try:
        print("pollA...")
        dispatch_jobs = DispatchJob.objects.filter(active=True)
        for d_job in dispatch_jobs:
            jobs = jobs_api_call(ids=d_job.job_id)
            if len(jobs) == 0:
                # Job was deleted
                d_job.delete()
                continue
            job = jobs[0]
            if "jobStatus" in job and job["jobStatus"] in ["Canceled", "Hold"]:
                d_job.active = False
                d_job.save()
                continue
            appointment_assignments = appointment_assignments_api_call(appointmentIds=d_job.appointment_id)
            if len(appointment_assignments) == 0:
                # Appointment canceled
                d_job.active = False
                continue
            #
            #   Find tech's data in list, if it is there
            #
            techusers = [str(o.st_id) for o in UserProfile.objects.all()]

            for assignment in appointment_assignments:
                if str(assignment["technicianId"]) not in techusers:
                    # This would be a ride-along or helper
                    continue
                #
                #   In the case of multiple techs, we need to be working with the right assignment for the d_job tech_id
                #
                if str(assignment["technicianId"]) != d_job.tech_id:
                    continue

                # Get user for push notifications
                try:
                    user_profile = UserProfile.objects.get(st_id=d_job.tech_id)
                    user = user_profile.user
                except UserProfile.DoesNotExist:
                    user = None

                if assignment["status"] == "Dispatched":
                    #
                    #   Ensure polling for "Working" starts
                    #
                    history_job, created = HistoryJob.objects.get_or_create(job_id=str(assignment["jobId"]),
                                                                            appointment_id=str(
                                                                                assignment["appointmentId"]))
                    d_job.polling_active = True
                    d_job.save()

                    # Check if history is ready and send push if not already notified
                    if history_job.ready and not d_job.notified_history and user:

                        send_tech_status_push(user, 3, data=history_job.data, job_id=d_job.job_id)
                        d_job.notified_history = True
                        d_job.save()
                        print(f"Sent history ready push (result:3) for job {d_job.job_id}")

                elif assignment["status"] == "Done":  # THIS CAN TRIGGER RECORDING STOP
                    print(f"Setting DispatchJob status to 'Done' for job {d_job.job_id}")
                    d_job.status = "Done"
                    d_job.polling_active = False
                    d_job.active = False

                    # Send push notification if not already sent
                    if not d_job.notified_done and user:

                        send_tech_status_push(user, 2, job_id=d_job.job_id)
                        d_job.notified_done = True
                        print(f"Sent done push (result:2) for job {d_job.job_id}")

                    d_job.save()

                elif assignment["status"] == "Scheduled":
                    d_job.status = "Scheduled"
                    d_job.polling_active = False
                    d_job.active = False
                    d_job.save()

                elif assignment["status"] == "Working":
                    print(f"Setting DispatchJob status to 'Working' for job {d_job.job_id}")
                    d_job.status = "Working"  # THIS IS WHAT TRIGGERS RECORDING START; don't set active to False
                    d_job.polling_active = True  # Should already be True; iOS polling will set to False when recording starts

                    # Send push notification if not already sent
                    if not d_job.notified_working and user:

                        send_tech_status_push(user, 1, job_id=d_job.job_id)
                        d_job.notified_working = True
                        print(f"Sent working push (result:1) for job {d_job.job_id}")

                    d_job.save()
                else:
                    print(f"Assignment error!  status = {assignment['status']}!!")
        now = timezone.now()
        if now.minute % 10 == 0:
            pollB()
    except Exception as e:
        print(f"PollA failed: {e}")


def pollB():  # A less-frequent polling to catch any job completions that didn't trigger a JobComplete Webhook
    try:
        print("10 minute poll...")
        dispatch_jobs = DispatchJob.objects.filter(active=True, status="Working")
        for d_job in dispatch_jobs:
            jobs = jobs_api_call(ids=d_job.job_id)
            if len(jobs) == 0:
                print(f"Job {d_job.job_id} not found; setting status to 'Done'")
                # Job was deleted (?) but recording may have started already
                d_job.status = "Done"
                d_job.active = False
                d_job.save()
                continue
            job = jobs[0]
            if "jobStatus" in job and job["jobStatus"] in ["Canceled", "Hold"]:
                # Recording may have started already
                d_job.status = "Done"
                d_job.active = False
                d_job.save()
                continue
            appointment_assignments = appointment_assignments_api_call(appointmentIds=d_job.appointment_id)
            if len(appointment_assignments) == 0:
                # Appointment canceled, but recording may have started already
                print(f"Appointment canceled (?) for job {d_job.job_id}")
                d_job.status = "Done"
                d_job.active = False
                d_job.save()
                continue
            #
            #   Find tech's data in list, if it is there
            #
            techusers = [str(o.st_id) for o in UserProfile.objects.all()]

            for assignment in appointment_assignments:
                if str(assignment["technicianId"]) not in techusers:
                    # This would be a ride-along or helper
                    continue
                #
                #   In the case of multiple techs, we need to be working with the right assignment for the d_job tech_id
                #
                if str(assignment["technicianId"]) != d_job.tech_id:
                    continue
                if assignment["status"] in ["Done", "Scheduled"]:  # Recording should not be happening; stop it if it is
                    print(f"Setting DispatchJob status to 'Done' for job {d_job.job_id}")
                    d_job.status = "Done"
                    d_job.polling_active = False
                    d_job.active = False
                    d_job.save()
                    continue
                if assignment["status"] == "Dispatched":  # Tech arrived on wrong job (??) Reset DispatchJob
                    print(f"Setting DispatchJob status back to 'Dispatched' for job {d_job.job_id}")
                    d_job.status = "Dispatched"
                    d_job.polling_active = True
                    d_job.active = True
                    d_job.save()
        cutoff = timezone.now() - timedelta(days=1)
        Task.objects.filter(stopped__lt=cutoff).delete()
    except Exception as e:
        print(f"PollB failed: {e}")


def compile_document(job_id):
    print("Compiling Document...")


def build_ai_job_document(dispatch_job_id):
    """
    Build comprehensive AI job document and upload to S3
    Called as background task when DispatchJob is created
    """
    try:
        dispatch_job = DispatchJob.objects.get(id=dispatch_job_id)


        print(f"Building AI document for job {dispatch_job.job_id}...")

        # Construct the document
        document_content = construct_job_document(
            dispatch_job.customer_id,
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

    except DispatchJob.DoesNotExist:
        print(f"❌ DispatchJob {dispatch_job_id} not found")
    except Exception as e:
        print(f"❌ Error building AI document: {e}")


def construct_job_document(customer_id, job_id=None, appointment_id=None, tech_id=None):
    """
    Construct comprehensive job data document from ServiceTitan API
    Easy to expand with additional data sources
    """
    document_parts = []

    if customer_id is not None:
        document_parts.append(get_invoices(customer_id))


    # Get job information
    '''jobs = jobs_api_call(ids=job_id)
    if jobs:
        job = jobs[0]
        document_parts.append("=== JOB INFORMATION ===")
        document_parts.append(f"Job Number: {job.get('jobNumber', 'N/A')}")
        document_parts.append(f"Job Status: {job.get('jobStatus', 'N/A')}")
        document_parts.append(f"Job Type: {job.get('jobType', {}).get('name', 'N/A')}")
        document_parts.append(f"Business Unit: {job.get('businessUnit', {}).get('name', 'N/A')}")
        document_parts.append(f"Campaign: {job.get('campaign', {}).get('name', 'N/A')}")
        document_parts.append(f"Summary: {job.get('summary', 'N/A')}")
        document_parts.append("")

        # Get customer information
        customer_id = job.get('customerId')
        if customer_id:
            customers = customers_api_call(ids=customer_id)
            if customers:
                customer = customers[0]
                document_parts.append("=== CUSTOMER INFORMATION ===")
                document_parts.append(f"Name: {customer.get('name', 'N/A')}")
                document_parts.append(f"Phone: {customer.get('phoneNumber', 'N/A')}")
                document_parts.append(f"Email: {customer.get('email', 'N/A')}")

                # Get address from customer
                address = customer.get('address', {})
                if address:
                    street = address.get('street', '')
                    city = address.get('city', '')
                    state = address.get('state', '')
                    zip_code = address.get('zip', '')
                    document_parts.append(f"Address: {street}, {city}, {state} {zip_code}")
                document_parts.append("")'''

    # Get appointment assignment information
    '''assignments = appointment_assignments_api_call(appointmentIds=appointment_id)
    if assignments:
        for assignment in assignments:
            if str(assignment.get('technicianId')) == str(tech_id):
                document_parts.append("=== APPOINTMENT INFORMATION ===")
                document_parts.append(f"Appointment Status: {assignment.get('status', 'N/A')}")

                start = assignment.get('arrivalWindowStart', 'N/A')
                end = assignment.get('arrivalWindowEnd', 'N/A')
                document_parts.append(f"Arrival Window: {start} to {end}")

                # Get technician name
                tech_name = TECHS.get(str(tech_id), 'Unknown')
                document_parts.append(f"Assigned Technician: {tech_name}")
                document_parts.append("")
                break'''

    # TODO: Add more data sources as needed:
    # - Estimates for this job
    # - Previous job history for this customer
    # - Equipment/location details
    # - Special notes or tags

    return "\n".join(document_parts)


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

def get_invoices(customer_id):
    print(f"Getting invoices for customer {customer_id}")
    one_year_ago = timezone.now() - timedelta(days=365)
    formatted_date = one_year_ago.strftime("%Y-%m-%d")
    invoices = invoices_api_call(invoicedOnOrAfter=formatted_date, customerId=customer_id)
    invoice_string = "Previous_Jobs_Done: "
    for invoice in invoices:
        if invoice["job"] is None:
            continue
        invoice_string += f"Date: {invoice['invoiceDate']}; Work done: {invoice['summary']}\n"
    return invoice_string