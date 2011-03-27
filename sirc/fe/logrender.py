import string
import cgi
import StringIO
import re
import logging

from google.appengine.api import memcache

import boto

import sirc.log
import sirc.logrender
from sirc.util import s3


def fetch_from_key(key):
  (log_data, suffix) = sirc.log.decode_id(key)
  s3path = 'rawlogs/%s/%s/%02d.%02d' % (log_data.channel,
                                        log_data.date.year,
                                        log_data.date.month,
                                        log_data.date.day)
  credentials = s3.get_credentials()
  conn = boto.connect_s3(credentials.access_key, credentials.secret,
                         debug=0)
  bucket_name = 'sirc'
  bucket = conn.create_bucket(bucket_name)
  s3key = boto.s3.key.Key(bucket)
  s3key.key = s3path
  data = s3key.get_contents_as_string()
  return data


def render_from_key(key):
  cached_data = memcache.get(key)
  if cached_data:
    return cached_data

  data = fetch_from_key(key)
  data = sirc.logrender.render_log(data)

  memcache.set(key, data, time=60)
  return data
