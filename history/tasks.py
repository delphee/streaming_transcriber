from django.utils import timezone
from st_api import jobs_api_call, appointment_assignments_api_call
from history.models import DispatchJob, HistoryJob
from streaming.models import UserProfile

def pollA():
    try:
        print("pollA...")
        dispatch_jobs = DispatchJob.objects.filter(active=True)
        for d_job in dispatch_jobs:
            jobs = jobs_api_call(jobNumber=d_job.id)
            if len(jobs)==0:
                # Job was deleted
                d_job.delete()
                continue
            job = jobs[0]
            if "jobStatus" in job and job["jobStatus"] in ["Canceled","Hold"]:
                d_job.active = False
                d_job.save()
                continue
            appointment_assignments = appointment_assignments_api_call(appointmentIds=d_job.appointmentId)
            if len(appointment_assignments)==0:
                # Appointment canceled
                d_job.active = False
                continue
            #
            #   Find tech's data in list, if it is there
            #
            tech_data = None
            techusers = [str(o.st_id) for o in UserProfile.objects.all()]
            for assignment in appointment_assignments:
                if str(assignment["technicianId"]) not in techusers:
                    d_job.active = False
                    d_job.save()
                    continue
                if assignment["status"] == "Dispatched":
                    #
                    #   Ensure polling for "Working" starts
                    #
                    HistoryJob.objects.get_or_create(job_id=str(assignment["jobId"]),appointment_id=str(assignment["appointmentId"]))
                    d_job.polling_active = True
                    d_job.save()
                elif assignment["status"] == "Done": # THIS IS WHAT TRIGGERS RECORDING STOP (Add Job Complete Webhook too?)
                    d_job.status = "Done"
                    d_job.polling_active = False
                    d_job.active = False
                    d_job.save()
                elif assignment["status"] == "Scheduled":
                    d_job.status = "Scheduled"
                    d_job.polling_active = False
                    d_job.active = False
                    d_job.save()
                elif assignment["status"] == "Working":
                    d_job.status = "Working"    # THIS IS WHAT TRIGGERS RECORDING START
                    d_job.polling_active = False
                    d_job.save()
                else:
                    print(f"Assignment error!  status = {assignment['status']}!!")
        now = timezone.now()
        if now.minute % 10 == 0:
            pollB()
    except Exception as e:
        print(f"PollA failed: {e}")


def pollB():
    print("10 minute poll...")