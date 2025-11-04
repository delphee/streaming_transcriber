


BUSINESS_UNIT_ASSOCIATIONS = {
    "ELECTRIC": [3027848, 3027849, 228980270],#Install, Svc, Gen Maint
    "PLUMBING": [3027853, 278, 3027852, 121862529, 121853840],#Svc, Maint, Install, Excavation, Drains
    "HVAC": [139736277, 4068002, 219855191, 3027851, 2025914, 10019073, 10019329] #Duct Cln, Install, Sales, Svc, MVP, Oil Svc, Oil MVP
}

DEPT_TO_GROUP = {dept_id: name for name, ids in BUSINESS_UNIT_ASSOCIATIONS.items() for dept_id in ids}

def get_group_ids(dept_id):
    group_name = DEPT_TO_GROUP.get(dept_id)
    return BUSINESS_UNIT_ASSOCIATIONS.get(group_name, [])