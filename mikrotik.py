import logging
import ipaddress
import librouteros

logger = logging.getLogger(__name__)


def get_dhcp_leases(host, username, password, port=8728):
    """Fetch IPv4 DHCP leases from /ip/dhcp-server/lease."""
    leases = []
    conn = librouteros.connect(host=host, username=username, password=password,
                               port=int(port))
    try:
        path = conn.path('/ip/dhcp-server/lease')
        for lease in path:
            ip = lease.get('active-address') or lease.get('address', '')
            mac = lease.get('active-mac-address') or lease.get('mac-address', '')
            if not ip or not mac:
                continue
            leases.append({
                'ip_address': ip.split('/')[0],
                'mac_address': mac.upper(),
                'hostname': lease.get('host-name') or lease.get('comment') or '',
            })
    except Exception as e:
        logger.warning(f"Could not fetch DHCP leases from {host}: {e}")
    finally:
        conn.close()
    return leases


def get_ppp_active(host, username, password, port=8728):
    """Fetch active PPPoE sessions from /ppp/active.

    MikroTik /ppp/active fields:
      - 'name': PPP interface name (e.g. pppoe-usr-0003) — used for IPv6 correlation
      - 'user': PPP username — the identity we track
      - 'address': assigned IPv4 address
      - 'caller-id': caller ID string (usually MAC address)
    """
    sessions = []
    conn = librouteros.connect(host=host, username=username, password=password,
                               port=int(port))
    try:
        path = conn.path('/ppp/active')
        for s in path:
            ip = s.get('address', '')
            uname = s.get('user') or s.get('name') or ''
            iface = s.get('name', '')
            caller = s.get('caller-id', '')
            if not ip or not uname:
                continue
            sessions.append({
                'username': uname,
                'ip_address': ip.split('/')[0],
                'interface': iface,
                'caller_id': caller,
            })
    except Exception as e:
        logger.warning(f"Could not fetch PPP active from {host}: {e}")
    finally:
        conn.close()
    return sessions


def get_ipv6_bindings(host, username, password, port=8728):
    """Fetch IPv6 bindings from /ipv6/dhcp-server/binding.

    The 'server' field in bindings corresponds to the DHCPv6 server name,
    which is typically the PPP interface name (e.g. pppoe-usr-0003).
    This is used to correlate IPv6 addresses/prefixes to PPPoE usernames.
    """
    bindings = []
    conn = librouteros.connect(host=host, username=username, password=password,
                               port=int(port))
    try:
        path = conn.path('/ipv6/dhcp-server/binding')
        for b in path:
            duid = b.get('duid', '')
            dhcp_server = b.get('server', '') or ''
            raw = b.get('address', '')
            if not raw:
                continue

            mac = b.get('mac-address', '')
            mac_up = mac.upper() if mac else None

            binding_type = 'address'
            ipv6_val = raw

            try:
                if '/' in raw:
                    net = ipaddress.ip_network(raw, strict=False)
                    if net.prefixlen < 128:
                        binding_type = 'prefix'
                        ipv6_val = str(net)
                    else:
                        ipv6_val = str(net.network_address)
            except ValueError:
                logger.warning(f"Could not parse IPv6 binding address '{raw}' from {host}")
                continue

            bindings.append({
                'ipv6_address': ipv6_val,
                'binding_type': binding_type,
                'duid': duid or None,
                'mac_address': mac_up,
                'interface': dhcp_server,
            })
    except Exception as e:
        logger.warning(f"Could not fetch IPv6 bindings from {host}: {e}")
    finally:
        conn.close()
    return bindings


def test_connection(host, username, password, port=8728):
    """Test connectivity to a MikroTik router. Returns (success, message)."""
    conn = None
    try:
        conn = librouteros.connect(host=host, username=username,
                                   password=password, port=int(port))
        identity = list(conn.path('/system/identity'))
        name = identity[0].get('name', 'Unknown') if identity else 'Unknown'
        return True, f"Connection successful. Router: {name}"
    except Exception as e:
        return False, str(e)
    finally:
        if conn:
            conn.close()
