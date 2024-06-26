#!/usr/local/bin/python
# Copyright © 2024 The Google Threat Intelligence authors. All Rights Reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Google Threat Intelligence MISP expansion module."""

from urllib import parse
import vt
import pymisp


MISP_ATTRIBUTES = {
    'input': [
        'hostname',
        'domain',
        'ip-src',
        'ip-dst',
        'md5',
        'sha1',
        'sha256',
        'url',
    ],
    'format': 'misp_standard',
}

MODULE_INFO = {
    'version': '1',
    'author': 'Google Threat Intelligence team',
    'description': ('An expansion module to have the observable\'s threat'
                    ' score assessed by Google Threat Intelligence.'),
    'module-type': ['expansion'],
    'config': [
        'apikey',
        'event_limit',
        'proxy_host',
        'proxy_port',
        'proxy_username',
        'proxy_password'
    ]
}

DEFAULT_RESULTS_LIMIT = 10


class GoogleThreatIntelligenceParser:
    """Main parser class to create the MISP event."""
    def __init__(self, client: vt.Client, limit: int) -> None:
        self.client = client
        self.limit = limit or DEFAULT_RESULTS_LIMIT
        self.misp_event = pymisp.MISPEvent()
        self.attribute = pymisp.MISPAttribute()
        self.parsed_objects = {}
        self.input_types_mapping = {
            'ip-src': self.parse_ip,
            'ip-dst': self.parse_ip,
            'domain': self.parse_domain,
            'hostname': self.parse_domain,
            'md5': self.parse_hash,
            'sha1': self.parse_hash,
            'sha256': self.parse_hash,
            'url': self.parse_url
        }
        self.proxies = None

    def query_api(self, attribute: dict) -> None:
        """Get data from the API and parse it."""
        self.attribute.from_dict(**attribute)
        self.input_types_mapping[self.attribute.type](self.attribute.value)

    def get_results(self) -> dict:
        """Serialize the MISP event."""
        event = self.misp_event.to_dict()
        results = {
            key: event[key] for key in ('Attribute', 'Object') \
                    if (key in event and event[key])
        }
        return {'results': results}

    def create_gti_report_object(self, report):
        """Create GTI report object."""
        report = report.to_dict()
        permalink = ('https://www.virustotal.com/gui/'
                     f"{report['type']}/{report['id']}")
        report_object = pymisp.MISPObject('Google-Threat-Intel-report')
        report_object.add_attribute('permalink', type='link', value=permalink)
        report_object.add_attribute(
            'Threat Score', type='text',
            value=get_key(
                report, 'attributes.gti_assessment.threat_score.value'))
        report_object.add_attribute(
            'Verdict', type='text',
            value=get_key(
                report, 'attributes.gti_assessment.verdict.value').replace(
                    'VERDICT_', ''))
        report_object.add_attribute(
            'Severity', type='text',
            value=get_key(
                report, 'attributes.gti_assessment.severity.value').replace(
                    'SEVERITY_', ''))
        report_object.add_attribute(
            'Threat Label', type='text',
            value=get_key(
                report, ('attributes.popular_threat_classification'
                         '.suggested_threat_label')))
        self.misp_event.add_object(**report_object)
        return report_object.uuid

    def parse_domain(self, domain: str) -> str:
        """Create domain MISP object."""
        domain_report = self.client.get_object(f'/domains/{domain}')

        # DOMAIN
        domain_object = pymisp.MISPObject('domain-ip')
        domain_object.add_attribute(
            'domain', type='domain', value=domain_report.id)

        report_uuid = self.create_gti_report_object(domain_report)
        domain_object.add_reference(report_uuid, 'analyzed-with')
        self.misp_event.add_object(**domain_object)
        return domain_object.uuid

    def parse_hash(self, file_hash: str) -> str:
        """Create hash MISP object."""
        file_report = self.client.get_object(f'/files/{file_hash}')
        file_object = pymisp.MISPObject('file')
        for hash_type in ('md5', 'sha1', 'sha256'):
            file_object.add_attribute(
                hash_type,
                **{'type': hash_type, 'value': file_report.get(hash_type)})

        report_uuid = self.create_gti_report_object(file_report)
        file_object.add_reference(report_uuid, 'analyzed-with')
        self.misp_event.add_object(**file_object)
        return file_object.uuid

    def parse_ip(self, ip: str) -> str:
        """Create ip MISP object."""
        ip_report = self.client.get_object(f'/ip_addresses/{ip}')

        # IP
        ip_object = pymisp.MISPObject('domain-ip')
        ip_object.add_attribute('ip', type='ip-dst', value=ip_report.id)

        report_uuid = self.create_gti_report_object(ip_report)
        ip_object.add_reference(report_uuid, 'analyzed-with')
        self.misp_event.add_object(**ip_object)
        return ip_object.uuid

    def parse_url(self, url: str) -> str:
        """Create URL MISP object."""
        url_id = vt.url_id(url)
        url_report = self.client.get_object(f'/urls/{url_id}')

        url_object = pymisp.MISPObject('url')
        url_object.add_attribute('url', type='url', value=url_report.url)

        report_uuid = self.create_gti_report_object(url_report)
        url_object.add_reference(report_uuid, 'analyzed-with')
        self.misp_event.add_object(**url_object)
        return url_object.uuid


def get_key(dictionary, key, default_value=''):
    """Get value from nested dictionaries."""
    dictionary = dictionary or {}
    keys = key.split('.')
    field_name = keys.pop()
    for k in keys:
        if k not in dictionary:
            return default_value
        dictionary = dictionary[k]
    return dictionary.get(field_name, default_value)


def get_proxy_settings(config: dict) -> dict:
    """Returns proxy settings in the requests format or None if not set up."""
    proxies = None
    host = config.get('proxy_host')
    port = config.get('proxy_port')
    username = config.get('proxy_username')
    password = config.get('proxy_password')

    if host:
        if not port:
            raise KeyError(
                ('The google_threat_intelligence_proxy_host config is set, '
                'please also set the virustotal_proxy_port.'))
        parsed = parse.urlparse(host)
        if 'http' in parsed.scheme:
            scheme = 'http'
        else:
            scheme = parsed.scheme
        netloc = parsed.netloc
        host = f'{netloc}:{port}'

        if username:
            if not password:
                raise KeyError(('The google_threat_intelligence_'
                                ' proxy_host config is set, please also'
                                ' set the virustotal_proxy_password.'))
            auth = f'{username}:{password}'
            host = auth + '@' + host

        proxies = {
            'http': f'{scheme}://{host}',
            'https': f'{scheme}://{host}'
        }
    return proxies


def dict_handler(request: dict):
    """MISP entry point fo the module."""
    if not request.get('config') or not request['config'].get('apikey'):
        return {
            'error': ('A Google Threat Intelligence api '
                      'key is required for this module.')
        }

    if not request.get('attribute'):
        return {
            'error': ('This module requires an "attribute" field as input,'
                      ' which should contain at least a type, a value and an'
                      ' uuid.')
        }

    if request['attribute']['type'] not in MISP_ATTRIBUTES['input']:
        return {'error': 'Unsupported attribute type.'}

    event_limit = request['config'].get('event_limit')
    attribute = request['attribute']

    try:
        proxy_settings = get_proxy_settings(request.get('config'))
        client = vt.Client(
            request['config']['apikey'],
            headers={
                'x-tool': 'MISPModuleGTIExpansion',
            },
            proxy=proxy_settings['http'] if proxy_settings else None)
        parser = GoogleThreatIntelligenceParser(
            client, int(event_limit) if event_limit else None)
        parser.query_api(attribute)
    except vt.APIError as ex:
        return {'error': ex.message}
    except KeyError as ex:
        return {'error': str(ex)}

    return parser.get_results()


def introspection():
    """Returns the module input attributes required."""
    return MISP_ATTRIBUTES


def version():
    """Returns the module metadata."""
    return MODULE_INFO


if __name__ == '__main__':
    # Testing/debug calls.
    import os
    api_key = os.getenv('GTI_API_KEY')
    # File
    request_data = {
        'config': {'apikey': api_key},
        'attribute': {
            'type': 'sha256',
            'value': ('ed01ebfbc9eb5bbea545af4d01bf5f10'
                      '71661840480439c6e5babe8e080e41aa')
        }
    }
    response = dict_handler(request_data)
    report_obj = response['results']['Object'][0]
    print(report_obj.to_dict())

    # URL
    request_data = {
        'config': {'apikey': api_key},
        'attribute': {
            'type': 'url',
            'value': 'http://47.21.48.182:60813/Mozi.a'
        }
    }
    response = dict_handler(request_data)
    report_obj = response['results']['Object'][0]
    print(report_obj.to_dict())

    # Ip
    request_data = {
        'config': {'apikey': api_key},
        'attribute': {
            'type': 'ip-src',
            'value': '180.72.148.38'
        }
    }
    response = dict_handler(request_data)
    report_obj = response['results']['Object'][0]
    print(report_obj.to_dict())

    # Domain
    request_data = {
        'config': {'apikey': api_key},
        'attribute': {
            'type': 'domain',
            'value': 'qexyhuv.com'
        }
    }
    response = dict_handler(request_data)
    report_obj = response['results']['Object'][0]
    print(report_obj.to_dict())
