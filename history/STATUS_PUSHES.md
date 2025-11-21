


Status 3:  AI Document has been built and is available for voice interaction; send Audible notification

Status 1:  Appointment shows tech is WORKING; send recording reminder notification (Audible)

Status 2:  Stops recording and makes DispatchJob InActive; send SILENT notification



Note: In tasks.py, it might be useful to check DeviceTokens and if there isn't one, go ahead and mark as notified so we
don't keep trying.











