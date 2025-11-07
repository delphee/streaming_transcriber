from django.utils import timezone
from history.st_api import jobs_api_call, appointment_assignments_api_call
from history.models import DispatchJob, HistoryJob
from streaming.models import UserProfile
from django_q.models import Task
from datetime import timedelta
from history.push_notifications import send_tech_status_push

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