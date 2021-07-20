import csv
import json
import re
import numpy as np
import requests

URL = 'https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/DINSMerge_wCounty/FeatureServer/0/query'
GEOCODE_URL = 'http://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates'
GEOCODE_MIN_SCORE = 80
DATA_CSV = 'damage0909.csv'


class Uncertain(Exception):
    pass


def get_addresses():
    with open(DATA_CSV, 'r') as csvfile:
        return list(csv.DictReader(csvfile))


def sanitize_address(address):
    # Remove suite/number/space, e.g. 123 Main St #A1 Chico, CA
    # Otherwise API won't come back with any results
    return re.sub(' #[A-Za-z0-9]+ ', ' ', address)


def geocode(address):
    address = sanitize_address(address)
    params = {
        'address': address,
        'searchExtent': {
            'xmin': -13658456.2423713,
            'ymin': 4035255.90431566,
            'xmax': -13044469.184815,
            'ymax': 4876992.54637059,
            'spatialReference': {
                'wkid': 102100
            },
        },
        'outSR': 102100,
        'f': 'pjson',
    }

    try:
        res = requests.get(GEOCODE_URL, params=params)
        res.raise_for_status()

        js = res.json()

        if not js['candidates']:
            raise ValueError('No geocode candidates')

        best_candidate = js['candidates'][0]
        if best_candidate['score'] < GEOCODE_MIN_SCORE:
            raise ValueError('Geocode candidate score too low')

        return best_candidate['extent'], best_candidate['location']
    except requests.exceptions.RequestException as e:
        raise ValueError('Bad request: ' + str(e))
    except Exception as e:
        if 'error' in js:
            raise ValueError('Geocode error: ' + js['error']['message'])
        else:
            raise ValueError('Unexpected geocode exception: ' + str(e))


def get_status(extent, location):
    # Workaround for error "24305: The Polygon input is not valid
    # because the ring does not have enough distinct points. Each
    # ring of a polygon must contain at least three distinct
    # points"
    if extent['xmin'] == extent['xmax'] and extent['ymin'] == extent['ymax']:
        geometry_type = 'esriGeometryPoint'
        geometry = {'x': extent['xmin'], 'y': extent['ymin']}
    else:
        geometry_type = 'esriGeometryEnvelope'
        geometry = extent

    params = {
        'where': '0=0',
        'geometry': json.dumps(geometry),
        'geometryType': geometry_type,
        'spatialRel': 'esriSpatialRelIntersects',
        'outFields': '*',
        'f': 'pjson',
    }

    try:
        res = requests.get(URL, params=params)
        res.raise_for_status()
        js = res.json()

        if not js['features']:
            return 'No record'

        def dist_to(location):
            def dist(house):
                house = house['geometry']
                return (house['x'] - location['x'])**2 +\
                    (house['y'] - location['y']) **2
            return dist
        
        #check the distance
        damageSort = set([])
        for f in js['features']:
            f_loc = f['geometry']
            fx =f_loc['x']
            fy =f_loc['y']
            f_dist = (fx-location['x'])**2+(fy-location['y'])**2
            if f_dist < 2000.0:
                 damageSort.add(f['attributes']['DAMAGE'] )
        print(damageSort)
        if  len(damageSort) > 1 or len(damageSort) == 0:
            
            raise Uncertain('Nearby features vary in damage')

        #return js['features'][0]['attributes']['DAMAGE']
        finalDamage = list(damageSort)
        
        return finalDamage[0]
    
    except Uncertain:
        raise
    except requests.exceptions.RequestException as e:
        raise ValueError('Bad request: ' + str(e))
    except Exception as e:
        if 'error' in js:
            raise ValueError('Status error: ' + js['error']['details'][0])
        else:
            raise ValueError('Unexpected status exception: ' + str(e))


if __name__ == '__main__':
    count = 0
    breakdown = {
        'certain': 0,
        'manually_reviewed': 0,
        'match': 0,
        'mismatch': 0,
        'uncertain': 0,
        'unverified': 0,
    }
    with open('damage_outcome.csv', 'w') as f:
        writer = csv.writer(f)
        writer.writerow(['Intake number', 'Address', 'Manual Status', 'Status', 'Verification'])

        for i in get_addresses():
            intake_num = i['Intake #']
            address = i['Full Addr']
            manual_status = i.get('Damage', 'No manual status')

            try:
                extent, location = geocode(address)
                status = get_status(extent, location)
            except Uncertain as e:
                status = 'Uncertain ({})'.format(e)
            except ValueError as e:
                status = 'Error ({})'.format(e)

            if manual_status:
                if status.startswith('Uncertain'):
                    verification = 'uncertain'
                else:
                    breakdown['certain'] += 1
                    if status == manual_status:
                        verification = 'match'
                    else:
                        verification = 'mismatch'
                breakdown['manually_reviewed'] += 1
            else:
                verification = 'unverified'

            count += 1
            breakdown[verification] += 1
            writer.writerow([intake_num, address, manual_status, status, verification])
            f.flush()
            

    print("""
* Total intake of {count}
* This script was able to determine the damage of {certain} addresses ({certain_pct:.2f}%)
* {manually_reviewed} of {count} addresses have been manually reviewed.
* Out of these automatically reviewed addresses, {match} matched the manually reviewed damage ({match_pct:.2f}%)
""".format(
        count=count,
        certain_pct=100.0 * breakdown['certain'] / count,
        match_pct=100.0 * breakdown['match'] / breakdown['certain'],
        **breakdown))
    print("done!")
