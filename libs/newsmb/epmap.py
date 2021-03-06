import sys
import uuid
import logging
from struct import pack, unpack, calcsize

if "." not in sys.path:
    sys.path.append(".")

from libs.newsmb.libdcerpc import DCERPC, DCERPCContext, DCERPCException, RPC_C_EP_ALL_ELTS
from libs.newsmb.libsmb import assert_unicode

EPT_MAP = 0x3
EPT_LOOKUP = 0x2

class EPMAP(DCERPC):
    def __init__(self, binding, getsock=None, username=None, password=None, computer=None, domain=None, frag_level=None, smbport=445):
        DCERPC.__init__(self, binding, getsock, username, password, computer, domain, frag_level, smbport=smbport)

    def ept_map(self, uuid, version):
        tower = ''
        tower += pack('<H', 5) #5 Floors
        tower += pack('<HB', 0x13, 0xd)        #Floor 1
        tower += DCERPCContext(uuid=uuid, version=version).tower_pack()
        tower += pack('<HH', 2, 0)
        tower += pack('<HB', 0x13, 0xd)        #Floor 2
        tower += DCERPCContext(uuid = u'8a885d04-1ceb-11c9-9fe8-08002b104860', version = u'2.0').tower_pack()
        tower += pack('<HH', 2, 0)
        tower += pack('<HBHH', 1, 0xb, 2, 0)   #Floor 3
        tower += pack('<HBH', 1, 0x7, 2)       #Floor 4
        tower += pack('>H', 135)
        tower += pack('<HBHL', 1, 0x9, 4, 0)   #Floor 5

        data = ''
        data += pack('<L', 1)
        data += '\0' * 16
        data += pack('<L', 2)
        data += pack('<LL', len(tower), len(tower))
        data += tower
        size = len(data) % 4
        if size != 0:
            data += '\0' * (4 - size)
        data += '\0' * 20
        data += pack('<L', 4)

        self.call(EPT_MAP, data, response = True)

        data = self.reassembled_data
        logging.debug('%s' % data.encode('hex'))
        status = unpack('<L', data[-4:])[0]
        if status != 0:
            logging.debug('Unexpected status: %d' % status)
        p = calcsize('<L16sLLLL')
        if len(data) < p:
            logging.debug('Not enough data')
        _, _, n_towers, _, _, _ = unpack('<L16sLLLL', data[:p])
        data = data[p:]
        for i in range(n_towers):
            p = calcsize('<LLLH')
            _, size, _, n_floors = unpack('<LLLH', data[:p])
            data = data[p:]
            for j in range(n_floors):
                p = calcsize('<H')
                lhs_length = unpack('<H', data[:p])[0]
                data = data[p:]
                key = data[:lhs_length]
                data = data[lhs_length:]
                if lhs_length > 0:
                    p = calcsize('<B')
                    protocol = unpack('<B', key[:p])[0]
                    key = key[p:]
                    if protocol == 0x0d: #uuid
                        p = calcsize('<16sBB')
                        uuid, major_version, minor_version = unpack('<16sBB', key[:p])
                    elif protocol == 0x0b: #?
                        pass
                    elif protocol == 0x07: #port
                        pass
                    elif protocol == 0x09: #ip
                        pass
                p = calcsize('<H')
                if len(data) < 2:
                    logging.debug('Not enough data')
                    return

                rhs_length = unpack('<H', data[:p])[0]
                data = data[p:]
                value = data[:rhs_length]
                data = data[rhs_length:]
                logging.debug('%s:%s'%(key.encode('hex'),value.encode('hex')))


    def ept_lookup(self, handle):
        """
        Lookup HANDLE (20-byte buffer) and return dict of results or None.

        Dict should contain the following keys:
        'handle' -> 20 bytes long buffer, this can be reused in subsequent calls
        'annotation length'

        Dict may contain the following keys:
        'uuid'
        'version'
        'tcp'
        'udp'
        'ip'
        'netbios'
        'http'
        'ncalrpc'
        """
        result = {}
        data = ''
        data += pack('<L', RPC_C_EP_ALL_ELTS)
        data += '\0'*4 # NULL object reference
        data += '\0'*4 # NULL interface reference
        data += '\0'*4 # Version
        data += handle # Initial handle
        data += pack('<L', 1) # Maxents

        try:
            self.call(EPT_LOOKUP, data, response=True)
        except DCERPCException, ex:
            logging.debug('EPT Lookup exception: %s' % ex)
            return None

        data = self.reassembled_data

        if data == '':
            return None # Should raise Exception, probably FAULT

        status = unpack('<L', data[-4:])[0]
        if status != 0: # Should raise Exception
            logging.debug('EPT Lookup response status is: %x' % status)
            return None

        result['handle'], data = data[:20], data[20:]
        numents = unpack('<L', data[:4])[0]

        if numents == 0:
            return None

        data = data[40:]
        # load the annotation
        annotation_length, data = unpack('<L', data[:4])[0], data[4:]
        result['annotation length'] = annotation_length

        # here we also correct for mod 4 padding
        pad_length = 4 - annotation_length % 4

        if pad_length == 4:
            pad_length = 0

        # here we skip some stuff (tower lengths)?
        data = data[(annotation_length+pad_length)+8:]
        floors, data = unpack('<H', data[:2])[0], data[2:]
        floor = 1

        while floor <= floors:
            tint, data = unpack('<H', data[:2])[0], data[2:]
            if floor == 1:
                result['uuid'] = data[1:17]
                result['version'] = unpack('<H', data[17:19])[0]
            elif floor in (2, 3):
                pass
            else:
                address_type = ord(data[0])

            # second tint
            data = data[tint:]
            tint, data = unpack('<H', data[:2])[0], data[2:]
            backup = data

            if floor > 3:
                if address_type == 0x07:
                    result['tcp'] = unpack('>H', data[:2])[0]
                elif address_type == 0x08:
                    result['udp'] = unpack('>H', data[:2])[0]
                elif address_type == 0x09:
                    result['ip'] = u'%s.%s.%s.%s' % (ord(data[0]),
                                                     ord(data[1]),
                                                     ord(data[2]),
                                                     ord(data[3]))
                elif address_type == 0x0f:
                    index=data.find("\x00")
                    result['np'] = unicode(data[:index])
                elif address_type == 0x10:
                    index = data.find("\x00")
                    result['ncalrpc'] = unicode(data[:index])
                elif address_type == 0x11:
                    index = data.find("\x00")
                    result['netbios'] = unicode(data[:index])
                elif address_type == 0x1f:
                    result['http'] = unpack('>H', data[:2])[0]
                else:
                    logging.debug("Unknown address type: %d (floor %d, %d bytes)" % (address_type, floor, tint))

            floor += 1
            data = backup[tint:]

        if result['handle'][4:] == '\x00'*16:
            return None

        return result

    def dump(self):
        """
        Exhaustively call ept_lookup and return list of results (dictionaries).
        """
        handle = '\0'*20
        ret = True
        result = []

        while ret != None:
            ret = self.ept_lookup(handle)
            if ret != None:
                result.append(ret)
                handle = ret['handle']

        return result


class EPTHandle:
    def __init__(self, handle_dict=None):
        self.handle_dict = handle_dict

        if self.handle_dict == None:
            self.handle_dict = {}
            self.handle_dict['handle'] =  '\0'*20

    def getuuid(self):
        return uuid.UUID(bytes_le = self.handle_dict['uuid'])

    def getversion(self):
        return self.handle_dict.get('version')

    def get_type_info(self):
        type_info = ''

        if 'tcp' in self.handle_dict:
            type_info += u'tcp:%d:' % self.handle_dict['tcp']

        if 'udp' in self.handle_dict:
            type_info += u'udp:%d:' % self.handle_dict['udp']

        if 'netbios' in self.handle_dict:
            type_info += u'netbios:%s:' % self.handle_dict['netbios']

        if 'np' in self.handle_dict:
            type_info += u'namedpipe:%s:' % self.handle_dict['np']

        if 'http' in self.handle_dict:
            type_info += u'http:%s:' % self.handle_dict['http']

        if 'ip' in self.handle_dict:
            type_info += u'ip:%s:' % self.handle_dict['ip']

        if 'ncalrpc' in self.handle_dict:
            type_info += u'ncalrpc:%s:' % self.handle_dict['ncalrpc']

        return type_info


    def getinfo(self):
        u         = self.getuuid()
        version   = self.getversion()
        type_info = self.get_type_info()

        return u'%s:%d:%s' % (u, version, type_info)

    def getendpoint(self, ip):
        """
        Gets a nicely displayed and compatible endpoint.
        ip argument is only used when it is not provided internally, for named pipes.
        """
        if 'tcp' in self.handle_dict:
            return u'ncacn_ip_tcp:%s[%d]' % (self.handle_dict['ip'],
                                             self.handle_dict['tcp'])
        elif 'udp' in self.handle_dict:
            return u'ncacn_ip_udp:%s[%d]' % (self.handle_dict['ip'],
                                             self.handle_dict['udp'])
        elif 'np' in self.handle_dict:
            return u'ncacn_np:%s[%s]' % (ip, self.handle_dict['np'])
        elif 'http' in self.handle_dict:
            return u'ncacn_http:%s[%d]' % (self.handle_dict['ip'],
                                           self.handle_dict['http'])
        elif 'ncalrpc' in self.handle_dict:
            return u'ncalrpc:[%s]' % self.handle_dict['ncalrpc']
        else:
            return u''

    def isUUID(self, UUID):
        UUID = assert_unicode(UUID)
        return UUID == unicode(uuid.UUID(bytes_le=self.handle_dict['uuid']))

    def isRemote(self):
        return self.isHTTP() or self.isNP() or self.isTCP() or self.isUDP()

    def isTCP(self):
        return 'tcp' in self.handle_dict

    def isUDP(self):
        return 'udp' in self.handle_dict

    def isNP(self):
        return 'np' in self.handle_dict

    def isHTTP(self):
        return 'http' in self.handle_dict

    def __str__(self):
        return self.getendpoint(self.handle_dict['ip'])


if __name__ == '__main__':

    if len(sys.argv) > 1 and sys.argv[1] == '-v':
        logger = logging.getLogger()
        logger.setLevel(logging.DEBUG)

    ip = '10.0.0.1'
    epm = EPMAP(u'ncacn_ip_tcp:%s[135]' % (ip), getsock=None)
    epm.bind(u'e1af8308-5d1f-11c9-91a4-08002b14a0fa', u'3.0')
    try:
        print epm.dump()
        epm.ept_map(u'12345678-1234-abcd-ef00-01234567cffb', u'1.0') #NetLogon UUID
        epm.ept_map(u'1FF70682-0A51-30E8-076D-740BE8CEE98B', u'1.0')
    except Exception as e:
        logging.error('A parsing error occured: %s' % str(e))
