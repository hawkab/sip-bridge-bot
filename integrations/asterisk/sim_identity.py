"""SIM identity from explicit gateway/CDR metadata, never from the remote caller."""
import re


def sms_identity(config, port):
    for item in getattr(config, 'SMS_SIM_PORTS', []):
        if str(item['port']) == str(port):
            return {'sim_port': item['port'], 'local_number': item['number']}
    return {'sim_port': None, 'local_number': ''}


def call_identity(config, rows):
    inbound = any('inbound-gsm' in str(row.get('dcontext', '')) for row in rows)
    for row in rows:
        match = re.search(r'(?:^|;)sim_port=([0-9]+)(?:;|$)', row.get('userfield') or '')
        if match:
            return sms_identity(config, match[1])
        if inbound:
            port = getattr(config, 'CALL_GSM_INBOUND_DIDS', {}).get(str(row.get('dst', '')))
            if port is not None:
                return sms_identity(config, port)
    if not inbound:
        routes = getattr(config, 'VOICE_CALLS_ROUTES', [])
        for row in rows:
            channel = str(row.get('dstchannel') or '')
            endpoint = channel.removeprefix('PJSIP/').rsplit('-', 1)[0]
            candidates = [r for r in routes if r['endpoint'] == endpoint]
            # Shared endpoints require an explicit SIM marker in the CDR.
            if len(candidates) == 1:
                return sms_identity(config, candidates[0]['port'])
    return {'sim_port': None, 'local_number': ''}
