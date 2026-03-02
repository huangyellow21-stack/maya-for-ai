# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json

# Maya 2020 (Py2.7) ships with urllib2
import urllib2


class HttpError(Exception):
    pass


def post_json(url, payload, timeout_s=60):
    data = json.dumps(payload).encode("utf-8")
    req = urllib2.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        resp = urllib2.urlopen(req, timeout=timeout_s)
        body = resp.read()
        code = getattr(resp, "getcode", lambda: 200)()
        if code < 200 or code >= 300:
            raise HttpError("HTTP %s: %s" % (code, body))
        if not body:
            return None
        return json.loads(body.decode("utf-8"))
    except urllib2.HTTPError as e:
        try:
            b = e.read()
        except Exception:
            b = ""
        raise HttpError("HTTPError %s: %s" % (getattr(e, "code", "?"), b))
    except urllib2.URLError as e:
        raise HttpError("URLError: %s" % (str(e)))
    except Exception as e:
        raise HttpError(str(e))


def get_json(url, timeout_s=60):
    req = urllib2.Request(url)
    try:
        resp = urllib2.urlopen(req, timeout=timeout_s)
        body = resp.read()
        code = getattr(resp, "getcode", lambda: 200)()
        if code < 200 or code >= 300:
            raise HttpError("HTTP %s: %s" % (code, body))
        if not body:
            return None
        return json.loads(body.decode("utf-8"))
    except urllib2.HTTPError as e:
        try:
            b = e.read()
        except Exception:
            b = ""
        raise HttpError("HTTPError %s: %s" % (getattr(e, "code", "?"), b))
    except urllib2.URLError as e:
        raise HttpError("URLError: %s" % (str(e)))
    except Exception as e:
        raise HttpError(str(e))

