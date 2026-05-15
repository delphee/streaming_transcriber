from streaming_transcriber.settings import ST_APP_KEY, ST_CLIENT_ID, ST_CLIENT_SECRET, TENANT_ID
from .models import AccessToken
import requests
from datetime import datetime, timedelta
import pytz
from requests.structures import CaseInsensitiveDict
import json
import logging

logger = logging.getLogger(__name__)


def get_access_token():
    tokens = AccessToken.objects.all()
    if len(tokens) > 0:
        token = tokens[0].token
        when = tokens[0].when
        diff = (datetime.now(pytz.utc) - when).total_seconds()
        if diff < 840:
            return token
    print("Need new token...")
    url = "https://auth.servicetitan.io/connect/token"
    headers = CaseInsensitiveDict()
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    data = f"grant_type=client_credentials&client_id={ST_CLIENT_ID}&client_secret={ST_CLIENT_SECRET}"
    try:
        resp = requests.post(url, headers=headers, data=data)
        if resp.status_code > 299:
            print(resp.text)
    except Exception as E:
        print("Error getting Access Token:")
        print(E)
        return None
    myJson = resp.json()

    try:
        access_token = myJson['access_token']
        print("New token obtained.")
        for token in tokens:
            token.delete()
        AccessToken.objects.create(token=access_token,when=datetime.now(pytz.utc))
    except Exception as E:
        print("Error saving new access token:", E)
        access_token = None
    return access_token

TECHNICIANSAPIFILTERSTRINGS = ['ids','name','active','createdOnOrAfter','createdBefore']
def technicians_api_call(tenant=TENANT_ID,ids=None,name=None,active=None,createdOnOrAfter=None,createdBefore=None):
    TECHNICIANSAPIFILTERS = [ids,name,active,createdBefore,createdOnOrAfter]
    urltext = ""
    for i in range(len(TECHNICIANSAPIFILTERS)):
        if TECHNICIANSAPIFILTERS[i] is not None:
            urltext += f"{TECHNICIANSAPIFILTERSTRINGS[i]}={TECHNICIANSAPIFILTERS[i]}&"
    baseurl = f'https://api.servicetitan.io/settings/v2/tenant/{tenant}/technicians?{urltext}'[:-1]
    print(baseurl)
    token = get_access_token()
    if token in ["",None]:
        print("No token")
        return []
    st_app_key = ST_APP_KEY
    headers = CaseInsensitiveDict()
    headers['Authorization'] = '{}'.format(token)
    headers['ST-App-Key'] = f'{st_app_key}'
    count = 0 # use to kill if goes off the rails
    page = 1
    hasMore = True
    data = []
    try:
        while hasMore and (count < 100):
            url = baseurl + f"&page={page}"
            print(url)
            resp = requests.get(url, headers=headers)
            print(resp.status_code)
            if int(resp.status_code) > 299:
                print(resp.text)
            response = json.loads(resp.text)
            hasMore = response["hasMore"]
            for datadict in response["data"]:
                data.append(datadict)
            page += 1
            count += 1
    except Exception as e:
        print("Error in technicians_api_call:",e)
        return []
    return data

INVOICESAPIFILTERSTRINGS = ['ids','statuses','jobId','jobNumber','businessUnitId','customerId','invoicedOnOrAfter',
                            'invoicedOnOrBefore','number','createdOnOrAfter','createdBefore']
def invoices_api_call(tenant=TENANT_ID,ids=None,statuses=None,jobId=None,jobNumber=None,businessUnitId=None,customerId=None,
                      invoicedOnOrAfter=None,invoicedOnOrBefore=None,number=None,createdOnOrAfter=None,
                      createdBefore=None):
    urltext = ""
    #print(str(type(statuses)))
    #print(str(type(statuses))=="<class 'list'>")
    if statuses is not None and str(type(statuses))=="<class 'list'>":
        temp = ""
        for status in statuses:
            temp+= f"{status}&statuses="
        statuses = temp[:-10]
    INVOICESAPIFILTERS = [ids,statuses,jobId,jobNumber,businessUnitId,customerId,invoicedOnOrAfter,invoicedOnOrBefore,
                          number,createdOnOrAfter,createdBefore]
    for i in range(len(INVOICESAPIFILTERS)):
        if INVOICESAPIFILTERS[i] is not None:
            urltext += f"{INVOICESAPIFILTERSTRINGS[i]}={INVOICESAPIFILTERS[i]}&"
    baseurl = f'https://api.servicetitan.io/accounting/v2/tenant/{tenant}/invoices?{urltext}'[:-1]
    print(baseurl)
    token = get_access_token()
    if token in ["",None]:
        print("No token")
        return []
    st_app_key = ST_APP_KEY
    headers = CaseInsensitiveDict()
    headers['Authorization'] = '{}'.format(token)
    headers['ST-App-Key'] = f'{st_app_key}'
    count = 0 # use to kill if goes off the rails
    page = 1
    hasMore = True
    data = []
    try:
        while hasMore and (count < 100):
            url = baseurl + f"&page={page}"
            print(url)
            resp = requests.get(url, headers=headers)
            print(resp.status_code)
            if int(resp.status_code) > 299:
                print(resp.text)
            response = json.loads(resp.text)
            hasMore = response["hasMore"]
            for datadict in response["data"]:
                data.append(datadict)
            page += 1
            count += 1
    except:
        return []
    return data

JOBSAPIFILTERSTRINGS = ['ids','number','projectId','jobStatus','appointmentStatus','firstAppointmentStartsOnOrAfter',
                        'firstAppointmentStartsBefore','appointmentStartsOnOrAfter','technicianId','customerId','locationId',
                        'soldById','jobTypeId','businessUnitId','invoiceId','createdBefore','createdOnOrAfter',
                        'modifiedBefore','modifiedOnOrAfter','completedOnOrAfter','completedBefore']

def jobs_api_call(tenant=TENANT_ID,ids=None,number=None,projectId=None,jobStatus=None,appointmentStatus=None,
                  firstAppointmentStartsOnOrAfter=None,firstAppointmentStartsBefore=None,appointmentStartsOnOrAfter=None,
                  technicianId=None,customerId=None,locationId=None,soldById=None,jobTypeId=None,businessUnitId=None,
                  invoiceId=None,createdBefore=None,createdOnOrAfter=None,modifiedBefore=None,modifiedOnOrAfter=None,
                  completedOnOrAfter=None,completedBefore=None):
    JOBSAPIFILTERS = [ids,number,projectId,jobStatus,appointmentStatus,firstAppointmentStartsOnOrAfter,
                      firstAppointmentStartsBefore,appointmentStartsOnOrAfter,technicianId,customerId,locationId,
                      soldById,jobTypeId,businessUnitId,invoiceId,createdBefore,createdOnOrAfter,
                      modifiedBefore,modifiedOnOrAfter,completedOnOrAfter,completedBefore]
    urltext = ""
    for i in range(len(JOBSAPIFILTERS)):
        if JOBSAPIFILTERS[i] is not None:
            urltext += f"{JOBSAPIFILTERSTRINGS[i]}={JOBSAPIFILTERS[i]}&"
    baseurl = f'https://api.servicetitan.io/jpm/v2/tenant/{tenant}/jobs?{urltext}'[:-1]
    token = get_access_token()
    if token in ["",None]:
        print("No token")
        return []
    st_app_key = ST_APP_KEY
    headers = CaseInsensitiveDict()
    headers['Authorization'] = '{}'.format(token)
    headers['ST-App-Key'] = f'{st_app_key}'
    count = 0 # use to kill if goes off the rails
    page = 1
    hasMore = True
    data = []
    try:
        while hasMore and (count < 10):
            url = baseurl + f"&page={page}"
            print(url)
            resp = requests.get(url, headers=headers)
            print(resp.status_code)
            if int(resp.status_code) > 299:
                print(resp.text)
            response = json.loads(resp.text)
            hasMore = response["hasMore"]
            for datadict in response["data"]:
                data.append(datadict)
            page += 1
            count += 1
    except:
        return []
    return data

APPOINTMENTSASSIGNMENTSAPIFILTERSTRINGS = ['ids','appointmentIds','jobId','technicianId','createdOnOrAfter','createdBefore']
def appointment_assignments_api_call(tenant=TENANT_ID,ids=None,appointmentIds=None,jobId=None,technicianId=None,createdOnOrAfter=None,createdBefore=None):
    APPOINTMENTSASSIGNMENTSAPIFILTERS = [ids,appointmentIds,jobId,technicianId,createdOnOrAfter,createdBefore]
    urltext = ""
    for i in range(len(APPOINTMENTSASSIGNMENTSAPIFILTERS)):
        if APPOINTMENTSASSIGNMENTSAPIFILTERS[i] is not None:
            urltext+=f"{APPOINTMENTSASSIGNMENTSAPIFILTERSTRINGS[i]}={APPOINTMENTSASSIGNMENTSAPIFILTERS[i]}&"
    baseurl = f'https://api.servicetitan.io/dispatch/v2/tenant/{tenant}/appointment-assignments?{urltext}'[:-1]
    token = get_access_token()
    if token in ["",None]:
        print("No token")
        return []
    st_app_key = ST_APP_KEY
    headers = CaseInsensitiveDict()
    headers['Authorization'] = '{}'.format(token)
    headers['ST-App-Key'] = f'{st_app_key}'
    count = 0 # use to kill if goes off the rails
    page = 1
    hasMore = True
    data = []
    try:
        while hasMore and (count < 10):
            url = baseurl + f"&page={page}"
            print(url)
            resp = requests.get(url, headers=headers)
            print(resp.status_code)
            response = json.loads(resp.text)
            hasMore = response["hasMore"]
            for datadict in response["data"]:
                data.append(datadict)
            page += 1
            count += 1
    except:
        return []
    return data


CUSTOMERSAPIFILTERSTRINGS = ['ids','createdBefore','createdOnOrAfter','name','street','city','state','zip','phone',
                             'active']
def customers_api_call(tenant=TENANT_ID,ids=None,createdBefore=None,createdOnOrAfter=None,name=None,street=None,city=None,
                       state=None,zip=None,phone=None,active=None):
    CUSTOMERSAPIFILTERS = [ids,createdBefore,createdOnOrAfter,name,street,city,state,zip,phone,active]
    urltext = ""
    for i in range(len(CUSTOMERSAPIFILTERS)):
        if CUSTOMERSAPIFILTERS[i] is not None:
            urltext += f"{CUSTOMERSAPIFILTERSTRINGS[i]}={CUSTOMERSAPIFILTERS[i]}&"
    baseurl = f'https://api.servicetitan.io/crm/v2/tenant/{tenant}/customers?{urltext}'[:-1]
    token = get_access_token()
    if token in ["",None]:
        print("No token")
        return []
    st_app_key = ST_APP_KEY
    headers = CaseInsensitiveDict()
    headers['Authorization'] = '{}'.format(token)
    headers['ST-App-Key'] = f'{st_app_key}'
    count = 0 # use to kill if goes off the rails
    page = 1
    hasMore = True
    data = []
    try:
        while hasMore and (count < 100):
            url = baseurl + f"&page={page}"
            print(url)
            resp = requests.get(url, headers=headers)
            print(resp.status_code)
            if int(resp.status_code) > 299:
                print(resp.text)
                quit()
            response = json.loads(resp.text)
            hasMore = response["hasMore"]
            for datadict in response["data"]:
                data.append(datadict)
            page += 1
            count += 1
    except:
        return []
    return data


LOCATIONSAPIFILTERSTRINGS = ['ids','createdBefore','createdOnOrAfter','name','street','city','state','zip','phone',
                             'active']
def locations_api_call(tenant=TENANT_ID,ids=None,createdBefore=None,createdOnOrAfter=None,name=None,street=None,city=None,
                       state=None,zip=None,phone=None,active=None):
    LOCATIONSAPIFILTERS = [ids,createdBefore,createdOnOrAfter,name,street,city,state,zip,phone,active]
    urltext = ""
    for i in range(len(LOCATIONSAPIFILTERS)):
        if LOCATIONSAPIFILTERS[i] is not None:
            urltext += f"{LOCATIONSAPIFILTERSTRINGS[i]}={LOCATIONSAPIFILTERS[i]}&"
    baseurl = f'https://api.servicetitan.io/crm/v2/tenant/{tenant}/locations?{urltext}'[:-1]
    token = get_access_token()
    if token in ["",None]:
        print("No token")
        return []
    st_app_key = ST_APP_KEY
    headers = CaseInsensitiveDict()
    headers['Authorization'] = '{}'.format(token)
    headers['ST-App-Key'] = f'{st_app_key}'
    count = 0 # use to kill if goes off the rails
    page = 1
    hasMore = True
    data = []
    try:
        while hasMore and (count < 100):
            url = baseurl + f"&page={page}"
            print(url)
            resp = requests.get(url, headers=headers)
            print(resp.status_code)
            if int(resp.status_code) > 299:
                print(resp.text)
                quit()
            response = json.loads(resp.text)
            hasMore = response["hasMore"]
            for datadict in response["data"]:
                data.append(datadict)
            page += 1
            count += 1
    except:
        return []
    return data


ESTIMATESAPIFILTERSTRINGS = ['ids','status','jobId','projectId','jobNumber','number','soldAfter','soldBefore','soldBy','createdOnOrAfter','locationId']
def estimates_api_call(tenant=TENANT_ID,ids=None,status=None,jobId=None,projectId=None,jobNumber=None,number=None,soldAfter=None,
                      soldBefore=None,soldBy=None,createdOnOrAfter=None,locationId=None):
    urltext = ""
    ESTIMATESAPIFILTERS = [ids,status,jobId,projectId,jobNumber,number,soldAfter,soldBefore,soldBy,createdOnOrAfter,locationId]
    for i in range(len(ESTIMATESAPIFILTERS)):
        if ESTIMATESAPIFILTERS[i] is not None:
            urltext += f"{ESTIMATESAPIFILTERSTRINGS[i]}={ESTIMATESAPIFILTERS[i]}&"
    baseurl = f'https://api.servicetitan.io/sales/v2/tenant/{tenant}/estimates?{urltext}'[:-1]
    token = get_access_token()
    if token in ["",None]:
        print("No token")
        return []
    st_app_key = ST_APP_KEY
    headers = CaseInsensitiveDict()
    headers['Authorization'] = '{}'.format(token)
    headers['ST-App-Key'] = f'{st_app_key}'
    count = 0 # use to kill if goes off the rails
    page = 1
    hasMore = True
    data = []
    try:
        while hasMore and (count < 30):
            url = baseurl + f"&page={page}"
            print(url)
            resp = requests.get(url, headers=headers)
            print(resp.status_code)
            response = json.loads(resp.text)
            hasMore = response["hasMore"]
            for datadict in response["data"]:
                data.append(datadict)
            page += 1
            count += 1
    except:
        return []
    return data


# === SERVICETITAN PHONE CALLS API ===

def get_calls_by_timerange(start_datetime, tenant=TENANT_ID):
    """
    Get calls within a 1-minute window from the specified datetime.

    Args:
        start_datetime: ISO format datetime string (e.g., '2024-01-15T10:30:00Z')
        tenant: ServiceTitan tenant ID

    Returns:
        list: Calls with recording URLs available, formatted as:
            [
                {
                    'id': call_id,
                    'receivedOn': datetime,
                    'duration': 'HH:MM:SS',
                    'from': phone_number,
                    'to': phone_number,
                    'direction': 'Inbound'/'Outbound',
                    'recordingUrl': url,
                    'agentName': name,
                    'customerName': name
                },
                ...
            ]
    """
    try:
        # Parse the datetime and create 1-minute window
        if isinstance(start_datetime, str):
            start_dt = datetime.fromisoformat(start_datetime.replace('Z', '+00:00'))
        else:
            start_dt = start_datetime

        end_dt = start_dt + timedelta(minutes=1)

        # Format for API (remove timezone info)
        start_str = start_dt.strftime('%Y-%m-%dT%H:%M:%S')
        end_str = end_dt.strftime('%Y-%m-%dT%H:%M:%S')

        logger.info(f"Fetching calls from {start_str} to {end_str}")

        access_token = get_access_token()
        if not access_token:
            logger.error("No access token available")
            return []

        headers = CaseInsensitiveDict()
        headers['Authorization'] = f'{access_token}'
        headers['ST-App-Key'] = f'{ST_APP_KEY}'

        all_calls = []
        has_more = True
        page = 1

        while has_more and page < 100:
            url = f"https://api.servicetitan.io/telecom/v3/tenant/{tenant}/calls"
            url += f"?createdOnOrAfter={start_str}&createdBefore={end_str}&page={page}"

            logger.info(f"Fetching page {page}")
            resp = requests.get(url, headers=headers, timeout=30)

            if resp.status_code != 200:
                logger.error(f"ServiceTitan API error: {resp.text}")
                raise ValueError(f"Failed to fetch calls: {resp.status_code}")

            data = resp.json()
            has_more = data.get("hasMore", False)
            calls = data.get("data", [])
            all_calls.extend(calls)
            page += 1

        # Filter calls that have recording URLs and format them
        calls_with_recordings = []
        for call_data in all_calls:
            lead_call = call_data.get('leadCall', {})
            if lead_call and lead_call.get('recordingUrl'):
                agent = lead_call.get('agent')
                agent_name = agent.get('name', 'Unknown') if agent else 'Unknown'

                customer = lead_call.get('customer')
                customer_name = customer.get('name', 'Unknown') if customer else 'Unknown'

                formatted_call = {
                    'id': lead_call.get('id'),
                    'receivedOn': lead_call.get('receivedOn'),
                    'duration': lead_call.get('duration'),
                    'from': lead_call.get('from'),
                    'to': lead_call.get('to'),
                    'direction': lead_call.get('direction'),
                    'recordingUrl': lead_call.get('recordingUrl'),
                    'agentName': agent_name,
                    'customerName': customer_name
                }
                calls_with_recordings.append(formatted_call)

        logger.info(f"Found {len(all_calls)} total calls, {len(calls_with_recordings)} with recordings")
        return calls_with_recordings

    except Exception as e:
        logger.error(f"Error fetching calls by timerange: {str(e)}")
        raise


def download_call_recording(call_id, tenant=TENANT_ID):
    """
    Download call recording as MP3.

    Args:
        call_id: ServiceTitan call ID
        tenant: ServiceTitan tenant ID

    Returns:
        bytes: MP3 audio data
    """
    try:
        access_token = get_access_token()
        if not access_token:
            raise ValueError("No access token available")

        headers = CaseInsensitiveDict()
        headers['Authorization'] = f'{access_token}'
        headers['ST-App-Key'] = f'{ST_APP_KEY}'

        url = f"https://api.servicetitan.io/telecom/v2/tenant/{tenant}/calls/{call_id}/recording"

        logger.info(f"Downloading recording for call {call_id}")
        resp = requests.get(url, headers=headers, stream=True, timeout=120)

        if resp.status_code > 299:
            logger.error(f"ServiceTitan API error: {resp.text}")
            raise ValueError(f"Failed to download recording: {resp.status_code}")

        # Collect all chunks into bytes
        audio_data = b''
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                audio_data += chunk

        logger.info(f"Downloaded {len(audio_data)} bytes for call {call_id}")
        return audio_data

    except Exception as e:
        logger.error(f"Error downloading call {call_id}: {str(e)}")
        raise




