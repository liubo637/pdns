#!/usr/bin/env python
#
# Shell-script style.

import os
import requests
import shutil
import subprocess
import sys
import tempfile
import time

SQLITE_DB = 'pdns.sqlite3'
WEBPORT = '5556'
WEBPASSWORD = '12345'

NAMED_CONF_TPL = """
# Generated by runtests.py
options { directory "../regression-tests/zones/"; };
zone "example.com" { type master; file "example.com"; };
"""

ACL_LIST_TPL = """
# Generated by runtests.py
# local host
127.0.0.1
::1
"""

REC_EXAMPLE_COM_CONF_TPL = """
# Generated by runtests.py
auth-zones+=example.com=../regression-tests/zones/example.com
"""

REC_CONF_TPL = """
# Generated by runtests.py
auth-zones=
forward-zones=
forward-zones-recurse=
experimental-api-config-dir=%(conf_dir)s
include-dir=%(conf_dir)s
"""

def ensure_empty_dir(name):
    if os.path.exists(name):
        shutil.rmtree(name)
    os.mkdir(name)


wait = ('--wait' in sys.argv)
if wait:
    sys.argv.remove('--wait')

daemon = (len(sys.argv) == 2) and sys.argv[1] or None
if daemon not in ('authoritative', 'recursor'):
    print "Usage: ./runtests (authoritative|recursor)"
    sys.exit(2)

daemon = sys.argv[1]

if daemon == 'authoritative':

    # Prepare sqlite DB with a single zone.
    subprocess.check_call(["rm", "-f", SQLITE_DB])
    subprocess.check_call(["make", "-C", "../pdns", "zone2sql"])

    with open('../modules/gsqlite3backend/schema.sqlite3.sql', 'r') as schema_file:
        subprocess.check_call(["sqlite3", SQLITE_DB], stdin=schema_file)

    with open('named.conf', 'w') as named_conf:
        named_conf.write(NAMED_CONF_TPL)
    with tempfile.TemporaryFile() as tf:
        p = subprocess.Popen(["../pdns/zone2sql", "--transactions", "--gsqlite", "--named-conf=named.conf"], stdout=tf)
        p.communicate()
        if p.returncode != 0:
            raise Exception("zone2sql failed")
        tf.seek(0, os.SEEK_SET)  # rewind
        subprocess.check_call(["sqlite3", SQLITE_DB], stdin=tf)

    pdnscmd = ("../pdns/pdns_server --daemon=no --local-port=5300 --socket-dir=./ --no-shuffle --launch=gsqlite3 --gsqlite3-dnssec --send-root-referral --allow-2136-from=127.0.0.0/8 --experimental-rfc2136=yes --cache-ttl=0 --no-config --gsqlite3-database="+SQLITE_DB+" --experimental-json-interface=yes --webserver=yes --webserver-port="+WEBPORT+" --webserver-address=127.0.0.1 --query-logging  --webserver-password="+WEBPASSWORD).split()

else:
    conf_dir = 'rec-conf.d'
    ensure_empty_dir(conf_dir)
    with open('acl.list', 'w') as acl_list:
        acl_list.write(ACL_LIST_TPL)
    with open('recursor.conf', 'w') as recursor_conf:
        recursor_conf.write(REC_CONF_TPL % locals())
    with open(conf_dir+'/example.com..conf', 'w') as conf_file:
        conf_file.write(REC_EXAMPLE_COM_CONF_TPL)

    pdnscmd = ("../pdns/pdns_recursor --daemon=no --socket-dir=. --config-dir=. --allow-from-file=acl.list --local-port=5555 --experimental-json-interface=yes --experimental-webserver=yes --experimental-webserver-port="+WEBPORT+" --experimental-webserver-address=127.0.0.1 --experimental-webserver-password="+WEBPASSWORD).split()


# Now run pdns and the tests.
print "Launching pdns..."
print ' '.join(pdnscmd)
pdns = subprocess.Popen(pdnscmd, close_fds=True)

print "Waiting for webserver port to become available..."
available = False
for try_number in range(0, 10):
    try:
        res = requests.get('http://127.0.0.1:%s/' % WEBPORT)
        available = True
        break
    except:
        time.sleep(0.5)

if not available:
    print "Webserver port not reachable after 10 tries, giving up."
    pdns.terminate()
    pdns.wait()
    sys.exit(2)

print "Running tests..."
rc = 0
test_env = {}
test_env.update(os.environ)
test_env.update({'WEBPORT': WEBPORT, 'WEBPASSWORD': WEBPASSWORD, 'DAEMON': daemon})

try:
    print ""
    p = subprocess.check_call(["nosetests", "--with-xunit"], env=test_env)
except subprocess.CalledProcessError as ex:
    rc = ex.returncode
finally:
    if wait:
        print "Waiting as requested, press ENTER to stop."
        raw_input()
    pdns.terminate()
    pdns.wait()

sys.exit(rc)
