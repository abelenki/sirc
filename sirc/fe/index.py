import sys
import re
import logging
import datetime
import hashlib
import urllib
import urllib2
import time

from google.appengine.ext import webapp
from google.appengine.ext import db
from google.appengine.ext import blobstore

import mapreduce.operation
import mapreduce.control
import mapreduce.context

import sirc.fe.tokenz


class QueryEvent(db.Model):
  timestamp = db.DateTimeProperty(required=True)
  query = db.StringProperty(required=True)
  query_time = db.FloatProperty()


def record_query(query):
  now = datetime.datetime.now()
  qe = QueryEvent(timestamp=now,
                  query=query)
  qe.put()
  return qe


LINE_RE = re.compile(r'([0-9]+:[0-9]+:[0-9]+) <(\w+)> ?(.*)', re.UNICODE)


def parse_log_line(line):
  return ircloglib.parse_line(line)


def index_log_line(entity):
  context = mapreduce.context.get()
  params = context.mapreduce_spec.mapper.params
  #logging.info('params: %s' % (params,))
  #logging.info('Got entity: %s' % (entity,))
  log_key = params['log_key']
  position, line = entity

  parsed_line = parse_log_line(line)
  if parsed_line:
    timestamp_str, user, text = parsed_line

    log = DayLog.get(params['log_key'])
    msg_hour = int(timestamp_str[0:2])
    msg_minute = int(timestamp_str[3:5])
    msg_second = int(timestamp_str[6:8])
    timestamp = datetime.datetime(log.date.year,
                                  log.date.month,
                                  log.date.day,
                                  msg_hour,
                                  msg_minute,
                                  msg_second)

    encoded = False
    try:
      text_u = tokenz.recode(text)
      terms_u = tokenz.extract_text_tokens(text_u)
      user_u = tokenz.recode(user)
      encoded = True
    except tokenz.EncodingError, e:
      logging.error('Unable to encode line %r: %s' % (text, e))

    if encoded:
      index = LogLineIndex(log=log,
                           position=position,
                           channel=log.channel,
                           timestamp=timestamp,
                           terms=terms_u,
                           user=user_u,
                           text=text_u)
      yield mapreduce.operation.db.Put(index)


class IndexingFinished(webapp.RequestHandler):
  def post(self):
    pass


def parse_query_string(query_string):
  logging.info('%r' % (query_string,))
  words = tokenz.extract_text_tokens(query_string)
  return words


def make_db_query_from_parsed_query(parsed_query):
  db_query = LogLineIndex.all()
  for word in parsed_query:
    db_query.filter('terms =', word)
  db_query.order('-timestamp')
  return db_query


def get_query_results(query_string):
  parsed_query = parse_query_string(query_string)
  db_query = query.make_multi_term_query(parsed_query)
  records = db_query.fetch(1000)
  for r in records:
    blob_reader = blobstore.BlobReader(r.log.blob.key())
    r.context = get_context_lines(blob_reader, r.position)
    #logging.info('context: %s' % (r.context,))
    #logging.info('Context: %s' % (r.context,))
  for r in records:
    r['text'] = get_log_line_from_position(blob_reader, r['position'])
    logging.info(r['text'])
  return records


SEARCH_SERVER_URL = 'http://ec2-184-72-93-240.compute-1.amazonaws.com:8983' + \
                    '/solr/select/?q=%s&version=2.2&start=%s&rows=%s' + \
                    '&wt=json&sort=timestamp+desc'


def get_query_results(query_string, start, num_results):
  start_time = time.time()
  import cgi
  from django.utils import simplejson as json
  import datetime

  query_event = record_query('q=%s, start=%s' % (query_string, start))

  url = SEARCH_SERVER_URL % (urllib.quote_plus(query_string),
                             start,
                             num_results)
  logging.info('URL=%s' % (url,))
  result = urllib2.urlopen(url)
  response = json.load(result)
  response['response']['QTime'] = response['responseHeader']['QTime']
  response = response['response']
  for r in response['docs']:
    r['timestamp'] = datetime.datetime.strptime(r['timestamp'],
                                                '%Y-%m-%dT%H:%M:%SZ')

#  for r in response['docs']:
#    blob_reader = blobstore.BlobReader(r.log.blob.key())
#    r.context = get_context_lines(blob_reader, r.position)
    #logging.info('context: %s' % (r.context,))
    #logging.info('Context: %s' % (r.context,))
#  for r in records:
#    r['text'] = get_log_line_from_position(blob_reader, r['position'])
  end_time = time.time()
  query_time = end_time - start_time
  response['query_time'] = query_time
  query_event.query_time = query_time
  query_event.put()
  return response


def get_log_line_from_position(blob_reader, position):
  pos, line = blob_read_line(blob_reader, offset=position)
  return line


def blob_read_line(blob_reader, offset=None):
  if offset:
    blob_reader.seek(offset)
  position = blob_reader.tell()
  return position, blob_reader.readline()


def get_context_lines(blob_reader, position, num_lines=5):
  # FIXME; handle edge cases (like very tiny logs).

  offset = 1000
  have_context = False
  following_lines = []
  preceding_lines = []

  # First get the preceding lines of context.
  while not have_context and position - offset >= 0:
    preceding_lines = []
    #logging.debug('offset = %s, position - offset = %s',
    #              offset, position - offset)
    blob_reader.seek(position - offset)
    # Sync to the next line.
    line_pos, line = blob_read_line(blob_reader)
    # Read lines until we hit the line of interest.
    while line_pos != position:
      line_pos, line = blob_read_line(blob_reader)
      preceding_lines.append(line)
    # Did we get enough lines of preceding context?
    if len(preceding_lines) < num_lines + 1:
      # No.  Try again, but look further back.
      offset = 2000
    else:
      have_context = True

  # Now get trailing lines of context.
  line_pos, line = blob_read_line(blob_reader)
  while line and len(following_lines) < num_lines:
    following_lines.append(line)
    line_pos, line = blob_read_line(blob_reader)

  context_lines = preceding_lines + following_lines
  context_lines = context_lines[-((num_lines * 2) + 1):]
  return context_lines


def blob_hash(blob_info):
  m = hashlib.md5()
  reader = blobstore.BlobReader(blob_info)
  try:
    m.update(reader.read())
    return m.hexdigest()
  finally:
    reader.close()


if __name__ == '__main__':
  for line in sys.stdin:
    parsed_line = parse_log_line(line)
    if parsed_line:
      timestamp, who, text = parsed_line
      words = extract_text_tokens(text)
      print words
