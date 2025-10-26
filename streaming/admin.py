from django.contrib import admin

# Register your models here.
from .models import *


admin.site.register(Conversation)
admin.site.register(Speaker)
admin.site.register(TranscriptSegment)
admin.site.register(ConversationAnalysis)
admin.site.register(UserProfile)
admin.site.register()