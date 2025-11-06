from django.utils import timezone
from st_api import jobs_api_call, appointment_assignments_api_call
from history.models import DispatchJob


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
            appointment_assignment = appointment_assignments_api_call(appointmentIds=d_job.appointmentId)
            if len(appointment_assignment)==0:
                # Appointment canceled
                d_job.active = False
                continue




        now = timezone.now()
        if now.minute % 10 == 0:
            pollB()
    except Exception as e:
        print(f"PollA failed: {e}")


def pollB():
    print("10 minute poll...")