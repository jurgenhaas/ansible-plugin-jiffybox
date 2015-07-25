import json
import requests

from ansible.callbacks import vv
from ansible.errors import AnsibleError as ae
from ansible.runner.return_data import ReturnData
from ansible.utils import parse_kv

class ActionModule(object):
    ''' Manage hosts and infrastructure at JiffyBox'''

    ### We need to be able to modify the inventory
    BYPASS_HOST_LOOP = True
    TRANSFERS_FILES = False

    def __init__(self, runner):
        self.runner = runner
        self.api_token = None
        self.url = None
        self.args = {}

        self.devices = []
        self.backups = []
        self.tarifs = []
        self.distros= []
        self.ips = []
        self.checks = []
        self.contacts = []
        self.documents = []

    def run(self, conn, tmp, module_name, module_args, inject, complex_args=None, **kwargs):

        if self.runner.noop_on_check(inject):
            return ReturnData(conn=conn, comm_ok=True, result=dict(skipped=True, msg='check mode not supported for this module'))

        if complex_args:
            self.args.update(complex_args)
        self.args.update(parse_kv(module_args))

        self.api_token = self._get_var('jiffy_api_token', self.args.get('api_token', False))
        if not self.api_token:
            raise ae("'api_token' is a required argument or you define the variable 'jiffy_api_token' in your inventory.")

        action = self.args.get('action', 'list')

        result = {}

        if action == 'list':
            self.list_devices()
        elif action == 'moveip':
            self.move_ip()
        else:
            raise ae("'%s' is an unknown action" % action)

        return ReturnData(conn=conn, comm_ok=True, result=result)

    def _get_var(self, key, default):
        firsthost = self.runner.host_set[0]
        variables = self.runner.inventory.get_variables(firsthost)
        if not variables.has_key(key):
            self.runner.inventory._vars_per_host[firsthost].__setitem__(key, default)
            result = default
        else:
            result = variables.get(key)
        return result

    def _request(self, path, data = None, method = 'GET'):
        encoder = json.JSONEncoder()
        postData = {}

        if self.url is None:
            self.url = 'https://api.jiffybox.de/' + self.api_token + '/v1.0/'

        if data:
            method = 'POST'
            for key in data:
                item = data.get(key)
                if type(item) is list or type(item) is dict:
                    if len(item) > 0:
                        item = encoder.encode(item)
                if type(item) is int or type(item) is unicode or type(item) is bool:
                    item = str(item)
                if item and type(item) is str and len(item) > 0:
                    postData.__setitem__(key, item)

        request_result = {}
        try:
            if method == 'GET':
                request_result = requests.get(self.url + path)
            elif method == 'POST':
                request_result = requests.put(self.url + path, data = postData)
            elif method == 'DELETE':
                request_result = requests.delete(self.url + path)
        except ae, e:
            raise ae('No result from JiffyBox API')

        decoder = json.JSONDecoder()
        content = decoder.decode(request_result.content)
        if not content['result']:
            msg = content['messages']
            raise ae('%s' % msg)
        return content['result']

    def _load_objects(self, type, path):
        vv("Reading %s from JiffyBox" % type)
        changed = False
        allgroup = self.runner.inventory.get_group('all')
        allvariables = allgroup.get_variables()
        if not allvariables.has_key('_jiffybox_' + type):
            changed = True
            objects = self._request(path)
            allgroup.set_variable('_jiffybox_' + type, objects)
        else:
            objects = allvariables.get('_jiffybox_' + type)
        return changed, objects

    def load_devices(self):
        if len(self.devices) == 0:
            (changed, self.devices) = self._load_objects('devices', 'jiffyBoxes')

    def load_ips(self):
        if len(self.ips) == 0:
            (changed, self.ips) = self._load_objects('ips', 'ips')

    def find_host(self, name):
        self.load_devices()
        for id in self.devices:
            if self.devices[id].get('name') == name:
                return self.devices[id]
        return False

    def find_host_by_ip(self, ip):
        self.load_devices()
        self.load_ips()
        for block in self.ips:
            if self.ips[block].has_key(str(ip.get('id'))):
                return self.devices[block]
        return False

    def find_ip(self, address):
        self.load_ips()
        for block in self.ips:
            for ip in self.ips[block]:
                if self.ips[block][ip].get('ip') == address:
                    return self.ips[block][ip]
        return False

    def find_floating_ip(self):
        self.load_ips()
        selected = self.args.get('ip', False)
        ips = []
        for block in self.ips:
            for ip in self.ips[block]:
                if self.ips[block][ip].get('floating') == 'true':
                    if ip == selected:
                        return self.ips[block][ip]
                    ips.append(self.ips[block][ip])
        if len(ips) == 0:
            raise ae('There is no floating ip address avaiulable.')
        if len(ips) == 1:
            return ips[0]

        #TODO: We should interactively select an IP here
        return False

    def list_devices(self):
        self.load_devices()
        columns = {'id': 10, 'name': 10, 'ips': 20}
        rows = []
        exist_floating = False
        for device in self.devices:
            id = str(self.devices[device].get('id'))
            name = self.devices[device].get('name')
            f_ips = []
            if name in self.runner.host_set:
                for address in self.devices[device].get('ips').get('public'):
                    ip = self.find_ip(address)
                    if ip:
                        address += ' [' + str(ip.get('id')) + ']'
                        if ip.get('floating') == 'true':
                            address += '*'
                            exist_floating = True
                    f_ips.append(address)
                ips = ' '.join(f_ips)
                if len(id) > columns['id']:
                    columns['id'] = len(id)
                if len(name) > columns['name']:
                    columns['name'] = len(name)
                if len(ips) > columns['ips']:
                    columns['ips'] = len(ips)
                rows.append([name, id, ips])
        rows.sort()
        rows.insert(0, ['Hostname', 'ID', 'IP [ID]'])
        rows.insert(1, [''.ljust(columns['name'], '-'), ''.ljust(columns['id'], '-'), ''.ljust(columns['ips'], '-')])
        output = "\n"
        for row in rows:
            output += row[0].ljust(columns['name']) + ' ' + row[1].ljust(columns['id']) + ' ' + row[2].ljust(columns['ips']) + "\n"
        if exist_floating:
            output += "\n* This marks a floating ip address\n"
        print(output)
        return ''

    def move_ip(self):
        vv("Moving IP ...")
        target = self.args.get('target', False)
        if not target:
            raise ae("'target' is a required argument.")
        targethost = self.find_host(target)
        if not targethost:
            raise ae("Target host %s unknown in this JiffyBox account." % target)
        vv("- target host: %s" % target)

        ip = self.find_floating_ip()
        if not ip:
            raise ae('No ip defined for moving.')
        vv("- floating IP: %s" % ip.get('ip'))

        sourcehost = self.find_host_by_ip(ip)
        if not sourcehost:
            raise ae('Current host for the floating ip can not be found.')
        vv("- source host: %s" % sourcehost.get('name'))

        if sourcehost.get('id') == targethost.get('id'):
            vv('- cancelled as source and target are the same')
            return

        path = 'ips/' + str(sourcehost.get('id')) + '/' + str(ip.get('id')) + '/move'
        self._request(path, {'targetid': targethost.get('id')})
