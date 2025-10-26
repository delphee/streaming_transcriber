from django.shortcuts import render

# Create your views here.
from django.http import JsonResponse

def health_check(request):
    return JsonResponse({
        'status': 'ok',
        'service': 'streaming_transcriber',
        'websocket_url': 'wss://desolate-dawn-05629-57e49d86c525.herokuapp.com/ws/stream/'
    })