from streaming_transcriber.settings import ST_APP_KEY, ST_CLIENT_ID, ST_CLIENT_SECRET, TENANT_ID
from .models import AccessToken
import requests
from datetime import datetime
import pytz
from requests.structures import CaseInsensitiveDict
import json


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
def invoices_api_call(tenant,ids=None,statuses=None,jobId=None,jobNumber=None,businessUnitId=None,customerId=None,
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

JOBSAPIFILTERSTRINGS = ['jobNumber','projectId','jobStatus','appointmentStatus','firstAppointmentStartsOnOrAfter',
                        'firstAppointmentStartsBefore','appointmentStartsOnOrAfter','technicianId','customerId','locationId',
                        'soldById','jobTypeId','businessUnitId','invoiceId','createdBefore','createdOnOrAfter',
                        'modifiedBefore','modifiedOnOrAfter','completedOnOrAfter','completedBefore']

def jobs_api_call(tenant,jobNumber=None,projectId=None,jobStatus=None,appointmentStatus=None,
                  firstAppointmentStartsOnOrAfter=None,firstAppointmentStartsBefore=None,appointmentStartsOnOrAfter=None,
                  technicianId=None,customerId=None,locationId=None,soldById=None,jobTypeId=None,businessUnitId=None,
                  invoiceId=None,createdBefore=None,createdOnOrAfter=None,modifiedBefore=None,modifiedOnOrAfter=None,
                  completedOnOrAfter=None,completedBefore=None):
    JOBSAPIFILTERS = [jobNumber,projectId,jobStatus,appointmentStatus,firstAppointmentStartsOnOrAfter,
                      firstAppointmentStartsBefore,appointmentStartsOnOrAfter,technicianId,customerId,locationId,
                      soldById,jobTypeId,businessUnitId,invoiceId,createdBefore,createdOnOrAfter,
                      modifiedBefore,modifiedOnOrAfter,completedOnOrAfter,completedBefore]
    print(tenant)
    print(completedBefore)
    print(completedOnOrAfter)
    urltext = ""
    for i in range(len(JOBSAPIFILTERS)):
        if JOBSAPIFILTERS[i] is not None:
            urltext += f"{JOBSAPIFILTERSTRINGS[i]}={JOBSAPIFILTERS[i]}&"
    baseurl = f'https://api.servicetitan.io/jpm/v2/tenant/{tenant}/jobs?{urltext}'[:-1]
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

APPOINTMENTSASSIGNMENTSAPIFILTERSTRINGS = ['ids','jobId','technicianId','createdOnOrAfter','createdBefore']
def appointment_assignments_api_call(tenant,ids=None,jobId=None,technicianId=None,createdOnOrAfter=None,createdBefore=None):
    APPOINTMENTSASSIGNMENTSAPIFILTERS = [ids,jobId,technicianId,createdOnOrAfter,createdBefore]
    urltext = ""
    for i in range(len(APPOINTMENTSASSIGNMENTSAPIFILTERS)):
        if APPOINTMENTSASSIGNMENTSAPIFILTERS[i] is not None:
            urltext+=f"{APPOINTMENTSASSIGNMENTSAPIFILTERSTRINGS[i]}={APPOINTMENTSASSIGNMENTSAPIFILTERS[i]}&"
    baseurl = f'https://api.servicetitan.io/dispatch/v2/tenant/{tenant}/appointment-assignments?{urltext}'[:-1]
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
def customers_api_call(tenant,ids=None,createdBefore=None,createdOnOrAfter=None,name=None,street=None,city=None,
                       state=None,zip=None,phone=None,active=None):
    CUSTOMERSAPIFILTERS = [ids,createdBefore,createdOnOrAfter,name,street,city,state,zip,phone,active]
    urltext = ""
    for i in range(len(CUSTOMERSAPIFILTERS)):
        if CUSTOMERSAPIFILTERS[i] is not None:
            urltext += f"{CUSTOMERSAPIFILTERSTRINGS[i]}={CUSTOMERSAPIFILTERS[i]}&"
    baseurl = f'https://api.servicetitan.io/crm/v2/tenant/{tenant}/customers?{urltext}'[:-1]
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


ESTIMATESAPIFILTERSTRINGS = ['ids','status','jobId','projectId','jobNumber','number','soldAfter','soldBefore','soldBy','createdOnOrAfter']
def estimates_api_call(tenant,ids=None,status=None,jobId=None,projectId=None,jobNumber=None,number=None,soldAfter=None,
                      soldBefore=None,soldBy=None,createdOnOrAfter=None):
    urltext = ""
    ESTIMATESAPIFILTERS = [ids,status,jobId,projectId,jobNumber,number,soldAfter,soldBefore,soldBy,createdOnOrAfter]
    for i in range(len(ESTIMATESAPIFILTERS)):
        if ESTIMATESAPIFILTERS[i] is not None:
            urltext += f"{ESTIMATESAPIFILTERSTRINGS[i]}={ESTIMATESAPIFILTERS[i]}&"
    baseurl = f'https://api.servicetitan.io/sales/v2/tenant/{tenant}/estimates?{urltext}'[:-1]
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
            response = json.loads(resp.text)
            hasMore = response["hasMore"]
            for datadict in response["data"]:
                data.append(datadict)
            page += 1
            count += 1
    except:
        return []
    return data




