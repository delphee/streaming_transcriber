

from django.core.management.base import BaseCommand
from django_q.tasks import schedule
from django_q.models import Schedule


class Command(BaseCommand):
    help = "Ensure recurring schedules exist"

    def handle(self, *args, **options):
        job_name = "check_database_every_minute"
        func_path = "history.tasks.pollA"

        sched = Schedule.objects.filter(name=job_name, func=func_path).first()

        if sched:
            self.stdout.write(f"✅ Schedule already exists: {job_name}")
        else:
            schedule(
                func_path,
                name=job_name,
                schedule_type=Schedule.MINUTES,
                minutes=1,
                repeats=-1,
            )
            self.stdout.write(f"🕒 Created schedule: {job_name}")