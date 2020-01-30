#!/usr/bin/env python3
from prometheus_client import MetricsHandler, Gauge, Info
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import time
import socket

# init dicts
metrics = {}
nodemap = {} # Will be used in get_metric_name
HOSTNAME = socket.gethostname()

# get rss of corosync process
def corss():
	try:
		corss_kb = int(subprocess.getoutput("grep VmRSS /proc/$(pidof corosync)/status |  grep kB | awk '{print $2}'"))
	except ValueError: # happens if corosync is down
		corss_kb = 0
	return ['stats.corosync.rss', '(u64)', '=', str(corss_kb*1024)]

# Converts graphite style metric names to prometheus metric names and labels
# Done on a per-metric basis
def get_metric_name(name):
	labels = {'hostname':HOSTNAME}
	tokes=name.split('.')
	if tokes[1] == 'knet':
		if tokes[2] == 'handle':
			retname = "knet_handle_"+tokes[3]
		else:
			retname = "knet_"+tokes[4]
			labels['node']=nodemap[tokes[2]]
			labels['link']=tokes[3]
	elif tokes[1] == 'srp' or tokes[1] == 'pg':
		retname = tokes[1]+"_"+tokes[2]
	elif tokes[1] == 'ipcs':
		if tokes[2] == 'global':
			retname = tokes[1]+"_"+tokes[2]+"_"+tokes[3]
		else:
			retname = tokes[1]+"_"+tokes[5]
			labels['service']=tokes[2]
			#labels['pid']=tokes[3]
			#labels['internalid']=tokes[4]
	elif tokes[1] == 'corosync':
		retname = 'corosync_rss'
	return retname, labels

# Reads metrics from corosync-cmapctl when called
def update():
	stdoutdata = subprocess.getoutput("corosync-cmapctl -m stats") # raw output
	interim = [line.split() for line in stdoutdata.split('\n')] # tokenize
	interim.append(corss()) # Add memory util to metrics list here to make things easy
	for i in interim:
		# Parse a metric line into prometheus data structures
		name, labels = get_metric_name(i[0])
		if name not in metrics:
			# Initialize metric objects and define labels if this metric is seen for the first time
			if i[1] != "(str)": # Gauge if not string
				# TODO: optimize type per metric
				metrics[name] = Gauge(name, '', labels.keys())
			else: # Only for ipcs_procname, because it's a string
				metrics[name] = Info(name, '', labels.keys())
		# Set metric values
		if i[1] != "(str)":
			metrics[name].labels(**labels).set(i[3])
		else:
			metrics[name].labels(**labels).info({'pname':i[3]})

# Had to extend MetricsHandler to update metrics only when a request is received
class CustomHandler(MetricsHandler):
	# Method override
	def do_GET(self):
		update() # Call to update metrics before serving to client
		super(CustomHandler, self).do_GET()

# Copied from prometheus_client/exposition.py because it's private,
# but we needed it to make start_custom_http_server
class _ThreadingSimpleServer(ThreadingMixIn, HTTPServer):
    """Thread per request HTTP server."""
    # Make worker threads "fire and forget". Beginning with Python 3.7 this
    # prevents a memory leak because ``ThreadingMixIn`` starts to gather all
    # non-daemon threads in a list in order to join on them at server close.
    # Enabling daemon threads virtually makes ``_ThreadingSimpleServer`` the
    # same as Python 3.7's ``ThreadingHTTPServer``.
    daemon_threads = True

# Copy of start_http_server, except that it uses CustomHandler
def start_custom_http_server(port, addr=''):
	"""Starts an HTTP server for prometheus metrics as a daemon thread"""
	httpd = _ThreadingSimpleServer((addr, port), CustomHandler)
	t = threading.Thread(target=httpd.serve_forever)
	t.daemon = True
	t.start()

if __name__ == '__main__':
	idmap = {}
	namemap = {}
	# One-time map generation for nodeID=>hostname
	# The index corosync stats assigns to a node is not the same as its ID
	# Example output of the below command:
	# nodelist.node.22.name (str) = hostname-goes-here
	# nodelist.node.22.nodeid (u32) = 26
	# nodelist.node.22.quorum_votes (u32) = 1
	# nodelist.node.22.ring0_addr (str) = 42.0.69.69
	# nodelist.node.22.ring1_addr (str) = 112.35.8.13
	stdoutdata = subprocess.getoutput("corosync-cmapctl nodelist.node")
	interim = [line.split() for line in stdoutdata.split('\n')]
	for i in interim:
		tokes=i[0].split('.')
		if tokes[3] == 'name': # Generate name=>statID map
			namemap[tokes[2]]=i[3]
		if tokes[3] == 'nodeid':
			idmap[tokes[2]]="node"+i[3] # Generate nodeID=>statID map
	for i in namemap: # From A and B, make name=>nodeID map
		nodemap[idmap[i]]=namemap[i]

	start_custom_http_server(8000)

	while True:
		time.sleep(1)
