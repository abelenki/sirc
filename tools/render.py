#!/usr/bin/env python
from __future__ import with_statement
import sys
import time
import logging
import optparse
import contextlib

import sirc.logrender


def main(args):
  parser = optparse.OptionParser(
    usage='usage: %prog [options] <log source>')
  parser.add_option(
    '-d',
    '--destination',
    dest='destination',
    default='-',
    help='Selects the output destination. Can be a file path, s3 url or ' +
         '"-" for stdout (defaults to %default).',
    metavar='')
  (options, args) = parser.parse_args()
  if len(args) != 1:
    parser.print_usage()
    return 1
  if options.destination == '-':
    destination = sys.stdout
  else:
    destination = sirc.log.open_log(options.destination, 'wb')

  print destination
  print args
  
  with contextlib.closing(destination):
    for path in args:
      print path
      with contextlib.closing(sirc.log.open_log(path, 'rb')) as input:
        log_text = input.read()
        time_start = time.time()
        html = sirc.logrender.render_log(log_text)
        time_end = time.time()
        destination.write(html)
        logging.info('Rendered %s in %s ms',
                     path,
                     int((time_end - time_start) * 1000))


if __name__ == '__main__':
  sys.exit(main(sys.argv))
