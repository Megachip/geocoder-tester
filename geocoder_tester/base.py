import json
import re

import requests
from geopy import Point
from geopy.distance import distance
from unidecode import unidecode
from pytest import skip

POTSDAM = [52.3879, 13.0582]
BERLIN = [52.519854, 13.438596]
MUNICH = [43.731245, 7.419744]
AUCKLAND = [-36.853467, 174.765551]
CONFIG = {
    'API_URL': "http://localhost:5001/api/",
    'API_TYPE': "default",
    'LOOSE_COMPARE': False,
    'MAX_RUN': 0,  # means no limit
    'GEOJSON': False,
    'FAILED': [],
}

http = requests.Session()


class HttpSearchException(Exception):

    def __init__(self, **kwargs):
        super().__init__()
        self.error = kwargs.get("error", {})

    def __str__(self):
        return self.error


class SearchException(Exception):
    """ custom exception for error reporting. """

    def __init__(self, params, expected, results, message=None):
        super().__init__()
        self.results = results
        if 'q' in params:
            self.query = params['q']
        elif 'lat' in params and 'lon' in params:
            self.query = '{lat}/{lon}'.format(**params)
        else:
            self.query = '???'
        self.params = params
        self.expected = expected
        self.message = message

    def __str__(self):
        lines = [
            '',
            'Search failed',
            "# Search was: {}".format(self.query),
        ]
        params = '# Params was: '
        params += " - ".join("{}: {}".format(k, v)
                             for k, v in self.params.items())
        lines.append(params)
        expected = '# Expected was: '
        expected += " | ".join("{}: {}".format(k, v)
                               for k, v in self.expected.items())
        lines.append(expected)
        if self.message:
            lines.append('# Message: {}'.format(self.message))
        lines.append('# Results were:')
        keys = [
            'name', 'osm_key', 'osm_value', 'osm_id', 'lat', 'lon', 'distance'
        ]
        for k in self.expected.keys():
            if k not in keys:
                keys.append(k)
        results = [self.flat_result(f) for f in self.results['features']]
        lines.extend(dicts_to_table(results, keys=keys))
        lines.append('')
        if CONFIG['GEOJSON']:
            coordinates = None
            if 'coordinate' in self.expected:
                coordinates = self.expected['coordinate'].split(',')[:2]
                coordinates.reverse()
                properties = self.expected.copy()
                properties.update({'expected': True})
            elif 'lat' in self.params and 'lon' in self.params:
                coordinates = [self.params['lon'], self.params['lat']]
                properties = {'center': True}
            if coordinates:
                coordinates = list(map(float, coordinates))
                geojson = self.to_geojson(coordinates, **properties)
                lines.append('# Geojson:')
                lines.append(geojson)
                lines.append('')
        return "\n".join(lines)

    def to_geojson(self, coordinates, **properties):
        self.results['features'].append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": coordinates},
            "properties": properties,
        })
        return json.dumps(self.results)

    def flat_result(self, result):
        out = None
        if 'geocoding' in result['properties']:
            out = result['properties']['geocoding']
        else:
            out = result['properties']
        if 'geometry' in result:
            out['lat'] = result['geometry']['coordinates'][1]
            out['lon'] = result['geometry']['coordinates'][0]
        else:
            out['lat'] = None
            out['lon'] = None

        out['distance'] = '—'
        if 'coordinate' in self.expected:
            lat, lon, max_deviation = map(float, self.expected['coordinate'].split(','))
            dist = distance(Point(lat, lon), Point(out['lat'], out['lon']))
            out['distance'] = int(dist.meters)
        return out

def search_params_default(query, limit, lang, center):
    params = {"q": query, "limit": limit}
    if lang:
        params['lang'] = lang
    if center:
        params['lat'] = center[0]
        params['lon'] = center[1]

    return CONFIG['API_URL'], params

def search_params_nominatim(query, limit, lang, center):
    params = {"format" : "geocodejson", "q" : query,"limit" : limit,
              "addressdetails" : "1"
             }
    if lang:
        params["accept-language"] = lang
    if center:
        skip(msg="API has no lat/lon search parameters")

    return CONFIG['API_URL'] + '/search', params

def search_params_photon(query, limit, lang, center):
    params = {"q": query, "limit": limit}
    if lang:
        params['lang'] = lang
    if center:
        params['lat'] = center[0]
        params['lon'] = center[1]

    return CONFIG['API_URL'] + '/api', params


def reverse_params_default(limit, lang, center, detail):
    params = {"q": '', "limit": limit}
    if lang:
        params['lang'] = lang
    if center:
        params['lat'] = center[0]
        params['lon'] = center[1]
    return CONFIG['API_URL'], params

def reverse_params_nominatim(limit, lang, center, detail):
    zoom = { 'country' : '3', 'state' : '5', 'county' : '8',
             'city' : '10', 'district' : '14', 'street' : '17',
             'house' : '18'}
    params = {"format" : "geocodejson", "limit" : limit,
              "addressdetails" : "1",
              "lat" : center[0], "lon" : center[1],
              "zoom" : zoom[detail if detail is not None else 'house']}
    if lang:
        params["accept-language"] = lang

    return CONFIG['API_URL'] + '/reverse', params

def reverse_params_photon(limit, lang, center, detail):
    params = {"lat" : center[0], "lon" : center[1]}
    if lang:
        params['lang'] = lang
    if detail == 'street':
        params['query_string_filter'] = 'osm_key:highway'

    return CONFIG['API_URL'] + '/reverse', params

def known_fields_photon():
   return {'osm_id', 'osm_type', 'osm_key', 'osm_value',
           'country', 'city', 'street', 'postcode', 'name', 'state'}

def normalize(s):
    return normalize_pattern.sub(' ', unidecode(s.lower()))
normalize_pattern = re.compile('[^\w]')


def compare_values(get, expected):
    if CONFIG['LOOSE_COMPARE']:
        return normalize(get) == normalize(expected)
    return get == expected


def assert_search(query, expected, limit=1,
                  comment=None, lang=None, center=None):
    search_func = globals().get('search_params_' + CONFIG['API_TYPE'], None)
    if search_func is None:
        raise RuntimeError("Unknown API type {}".format(CONFIG['API_TYPE']))
    url, params = search_func(query, limit, lang, center)

    r = http.get(url, params=params,
                 headers={'user-agent': 'geocode-tester/0.0.1'})
    if not r.status_code == 200:
        raise HttpSearchException(error="Non 200 response")

    return check_result(r.json(), params, expected, comment)

def assert_reverse(query, expected, limit=1,
                  comment=None, lang=None, center=None, detail=None):
    reverse_func = globals().get('reverse_params_' + CONFIG['API_TYPE'], None)
    if reverse_func is None:
        raise RuntimeError("Unknown API type {}".format(CONFIG['API_TYPE']))
    url, params = reverse_func(limit, lang, center, detail)

    r = http.get(url, params=params,
                 headers={'user-agent': 'geocode-tester/0.0.1'})
    if not r.status_code == 200:
        raise HttpSearchException(error="Non 200 response")

    return check_result(r.json(), params, expected, comment)

def check_result(results, params, expected, comment):
    known_fields = globals().get('known_fields_' + CONFIG['API_TYPE'], None)
    if known_fields:
        new_expected = {}
        for k in known_fields():
            if k in expected:
                new_expected[k] = expected[k]
        expected = new_expected
    def assert_expected(expected):
        found = False
        for r in results['features']:
            passed = True
            properties = None
            if 'geocoding' in r['properties']:
                properties = r['properties']['geocoding']
            else:
                properties = r['properties']
            failed = properties['failed'] = []
            for key, value in expected.items():
                value = str(value)
                if not compare_values(str(properties.get(key)), value):
                    # Value is not like expected. But in the case of
                    # coordinate we need to handle the tolerance.
                    if key == 'coordinate':
                        coord = r['geometry']['coordinates']
                        lat, lon, max_deviation = map(float, value.split(","))
                        deviation = distance(
                            Point(lat, lon),
                            Point(coord[1], coord[0])
                        )
                        if int(deviation.meters) <= int(max_deviation):
                            continue  # Continue to other properties
                        failed.append('distance')
                    passed = False
                    failed.append(key)
            if passed:
                found = True
        if not found:
            raise SearchException(
                params=params,
                expected=expected,
                results=results
            )

    if not isinstance(expected, list):
        expected = [expected]
    for s in expected:
        assert_expected(s)


def dicts_to_table(dicts, keys):
    if not dicts:
        return []
    # Compute max length for each column.
    lengths = {}
    for key in keys:
        lengths[key] = len(key) + 2  # Surrounding spaces.
    for d in dicts:
        for key in keys:
            i = len(str(d.get(key, '')))
            if i > lengths[key]:
                lengths[key] = i + 2  # Surrounding spaces.
    out = []
    cell = '{{{key}:^{length}}}'
    tpl = '|'.join(cell.format(key=key, length=lengths[key]) for key in keys)
    # Headers.
    out.append(tpl.format(**dict(zip(keys, keys))))
    # Separators line.
    out.append(tpl.format(**dict(zip(keys, ['—'*lengths[k] for k in keys]))))
    for d in dicts:
        row = {}
        l = lengths.copy()
        for key in keys:
            value = d.get(key) or '_'
            if key in d['failed']:
                l[key] += 10  # Add ANSI chars so python len will turn out.
                value = "\033[1;4m{}\033[0m".format(value)
            row[key] = value
        # Recompute tpl with lengths adapted to failed rows (and thus ansi
        # extra chars).
        tpl = '|'.join(cell.format(key=key, length=l[key]) for key in keys)
        out.append(tpl.format(**row))
    return out
