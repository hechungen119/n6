#!/bin/sh
sudo /usr/sbin/rsyslogd >/dev/null 2>&1
sudo service rsyslog start >/dev/null 2>&1
. /home/dataman/env/bin/activate
exec "$@"