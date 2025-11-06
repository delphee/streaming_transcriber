from django.utils import timezone


def pollA():
    print("polling...")
    now = timezone.now()
    if now.minute % 10 == 0:
        pollB()


def pollB():
    print("10 minute poll...")