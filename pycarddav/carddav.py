#!/usr/bin/env python
# vim: set ts=4 sw=4 expandtab sts=4:
# ----------------------------------------------------------------------------
# "THE BEER-WARE LICENSE" (Revision 42):
# <geier@lostpackets.de> wrote this file. As long as you retain this notice you
# can do whatever you want with this stuff. If we meet some day, and you think
# this stuff is worth it, you can buy me a beer in return Christian Geier
# ----------------------------------------------------------------------------
"""
classes and methods for pycarddav, the carddav class could/should be moved
to another module for better reusing
"""

from collections import namedtuple
import requests
import sys
import urlparse
import logging
import lxml.etree as ET


def get_random_href():
    """returns a random href"""
    import random
    tmp_list = list()
    for _ in xrange(3):
        rand_number = random.randint(0, 0x100000000)
        tmp_list.append("{0:x}".format(rand_number))
    return "-".join(tmp_list).upper()


DAVICAL = 'davical'
SABREDAV = 'sabredav'
UNKNOWN = 'unknown server'


class UploadFailed(Exception):
    """uploading the card failed"""
    pass


class PyCardDAV(object):
    """interacts with CardDAV server

    Since PyCardDAV relies heavily on Requests [1] its SSL verification is also
    shared by PyCardDAV [2]. For now, only the *verify* keyword is exposed
    through PyCardDAV.

    [1] http://docs.python-requests.org/
    [2] http://docs.python-requests.org/en/latest/user/advanced/

    raises:
        requests.exceptions.SSLError
        requests.exceptions.ConnectionError
    """

    def __init__(self, resource, debug='', user='', passwd='',
                 verify=True, write_support=False):
        split_url = urlparse.urlparse(resource)
        url_tuple = namedtuple('url', 'resource base path')
        self.url = url_tuple(resource,
                             split_url.scheme + '://' + split_url.netloc,
                             split_url.path)
        self.debug = debug
        self.session = requests.session()
        self.write_support = write_support
        self._settings = {'auth': (user, passwd,),
                          'verify': verify}
        self._default_headers = {"User-Agent": "pyCardDAV"}

    @property
    def verify(self):
        """gets verify from settings dict"""
        return self._settings['verify']

    @verify.setter
    def verify(self, verify):
        """set verify"""
        self._settings['verify'] = verify

    @property
    def headers(self):
        return dict(self._default_headers)

    def _check_write_support(self):
        """checks if user really wants his data destroyed"""
        if not self.write_support:
            sys.stderr.write("Sorry, no write support for you. Please check "
                             "the documentation.\n")
            sys.exit(1)

    def _detect_server(self):
        """detects CardDAV server type

        currently supports davical and sabredav (same as owncloud)
        :rtype: string "davical" or "sabredav"
        """
        response = requests.request('OPTIONS',
                                    self.url.base,
                                    headers=self.header)
        if "X-Sabre-Version" in response.headers:
            server = SABREDAV
        elif "X-DAViCal-Version" in response.headers:
            server = DAVICAL
        else:
            server = UNKNOWN
        logging.info(server + " detected")
        return server

    def get_abook(self):
        """does the propfind and processes what it returns

        :rtype: list of hrefs to vcards
        """
        xml = self._get_xml_props()
        abook = self._process_xml_props(xml)
        return abook

    def get_vcard(self, vref):
        """
        pulls vcard from server

        :returns: vcard
        :rtype: string
        """
        response = self.session.get(self.url.base + vref,
                                    headers=self.headers,
                                    **self._settings)
        return response.content

    def update_vcard(self, card, vref, etag):
        """
        pushes changed vcard to the server
        card: vcard as unicode string
        etag: str or None, if this is set to a string, card is only updated if
              remote etag matches. If etag = None the update is forced anyway
         """
         # TODO what happens if etag does not match?
        self._check_write_support()
        remotepath = str(self.url.base + vref)
        headers = self.headers
        headers['content-type'] = 'text/vcard'
        if etag is not None:
            headers['If-Match'] = etag
        self.session.put(remotepath, data=card, headers=headers,
                         **self._settings)

    def delete_vcard(self, vref, etag):
        """deletes vcard from server

        deletes the resource at vref if etag matches,
        if etag=None delete anyway
        :param vref: vref of card to be deleted
        :type vref: str()
        :param etag: etag of that card, if None card is always deleted
        :type vref: str()
        :returns: nothing
        """
        # TODO: what happens if etag does not match, url does not exist etc ?
        self._check_write_support()
        remotepath = str(self.url.base + vref)
        headers = self.headers
        headers['content-type'] = 'text/vcard'
        if etag is not None:
            headers['If-Match'] = etag
        result = self.session.delete(remotepath,
                                     headers=headers,
                                     **self._settings)
        if not result.ok:
            raise Exception(result.reason, result.content)
            # TODO define own exception type

    def upload_new_card(self, card):
        """
        upload new card to the server

        :param card: vcard to be uploaded
        :type card: unicode
        :rtype: tuple of string (path of the vcard on the server) and etag of
                new card (string or None)
        """
        self._check_write_support()
        for _ in range(0, 5):
            rand_string = get_random_href()
            remotepath = str(self.url.resource + '/' + rand_string + ".vcf")
            headers = self.headers
            headers['content-type'] = 'text/vcard'
            headers['If-None-Match'] = '*'
            response = requests.put(remotepath, data=card, headers=headers,
                                    **self._settings)
            if response.ok:
                parsed_url = urlparse.urlparse(remotepath)

                if response.headers['etag'] is None:
                    etag = ''
                else:
                    etag = response.headers['etag']

                return (parsed_url.path, etag)
        raise UploadFailed(response.reason)
            # TODO: should raise an exception if this is ever reached

    def _get_xml_props(self):
        """PROPFIND method

        gets the xml file with all vcard hrefs

        :rtype: str() (an xml file)
        """
        headers = self.headers
        headers['Depth'] = '1'
        response = self.session.request('PROPFIND',
                                        self.url.resource,
                                        headers=headers,
                                        **self._settings)
        try:
            if response.headers['DAV'].count('addressbook') == 0:
                sys.stderr.write("URL is not a CardDAV resource")
                sys.exit(1)
        except AttributeError:
            print("URL is not a DAV resource")
            sys.exit(1)
        return response.content

    @classmethod
    def _process_xml_props(cls, xml):
        """processes the xml from PROPFIND, listing all vcard hrefs

        :param xml: the xml file
        :type xml: str()
        :rtype: dict() key: vref, value: etag
        """
        namespace = "{DAV:}"

        element = ET.XML(xml)
        abook = dict()
        for response in element.iterchildren():
            if (response.tag == namespace + "response"):
                href = ""
                etag = ""
                insert = False
                for refprop in response.iterchildren():
                    if (refprop.tag == namespace + "href"):
                        href = refprop.text
                    for prop in refprop.iterchildren():
                        for props in prop.iterchildren():
                            if (props.tag == namespace + "getcontenttype" and
                                (props.text == "text/vcard" or
                                 props.text == "text/x-vcard")):
                                insert = True
                            if (props.tag == namespace + "getetag"):
                                etag = props.text
                        if insert:
                            abook[href] = etag
        return abook
