#!/usr/bin/env python

import boto3
import os
import sys
import re

import subprocess
import tempfile
import shutil
import storage
import gzip
import StringIO
import rpmfile
import hashlib
import json

try:
    import xml.etree.cElementTree as ET
except:
    import xml.etree.ElementTree as ET


def gunzip_string(data):
    fobj = StringIO.StringIO(data)
    decompressed = gzip.GzipFile(fileobj=fobj)

    return decompressed.read()

def file_checksum(file_name, checksum_type):
    h = hashlib.new(checksum_type)
    with open(file_name, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)

    return h.hexdigest()


def sign_metadata(repomdfile):
    """Requires a proper ~/.rpmmacros file. See <http://fedoranews.org/tchung/gpg/>"""
    cmd = ["gpg", "--detach-sign", "--armor", repomdfile]
    try:
        subprocess.check_call(cmd)
        print ("Successfully signed repository metadata file")
    except subprocess.CalledProcessError as e:
        print ("Unable to sign repository metadata '%s'" % (repomdfile))
        exit(1)

def setup_repository(repo):
    """Make sure a repo is present at repopath"""
    if repo._grab.storage.exists("repodata/repomd.xml"):
        return

    tmpdir = tempfile.mkdtemp()
    cmd = ['createrepo', '--no-database', tmpdir]
    subprocess.check_output(cmd)
    repo._grab.syncdir(os.path.join(tmpdir, "repodata"), "repodata")
    shutil.rmtree(tmpdir)


def parse_repomd(data):
    root = ET.fromstring(data)
    namespaces = {'repo': 'http://linux.duke.edu/metadata/repo'}

    filelists = None
    primary = None

    for child in root:
        if 'type' not in child.attrib:
            continue

        result = {}
        for key in ['checksum', 'open-checksum',
                    'timestamp', 'size', 'open-size']:
            result[key] = child.find('repo:' + key, namespaces).text
        result['location'] = child.find('repo:location', namespaces).attrib['href']

        if child.attrib['type'] == 'filelists':
            filelists = result
        elif child.attrib['type'] == 'primary':
            primary = result

    return filelists, primary

def parse_filelists(data):
    root = ET.fromstring(data)
    namespaces = {'filelists': 'http://linux.duke.edu/metadata/filelists'}

    packages = {}

    for child in root:
        if not child.tag.endswith('}package'):
            continue

        pkgid = child.attrib['pkgid']
        name = child.attrib['name']
        arch = child.attrib['arch']
        version = child.find('filelists:version', namespaces)
        version = {'ver': version.attrib['ver'],
                   'rel': version.attrib['rel'],
                   'epoch': version.attrib['epoch']}

        files = []
        for node in child.findall('filelists:file', namespaces):
            file_name = node.text
            file_type = 'file'

            if 'type' in node.attrib and node.attrib['type'] == 'dir':
                file_type = 'dir'
            files.append({'type': file_type, 'name': file_name})

        package = {'pkgid': pkgid, 'name': name, 'arch': arch,
                   'version': version, 'files': files}
        nerv = (name, version['epoch'], version['rel'], version['ver'])
        packages[nerv] = package


    return packages

def dump_filelists(filelists):
    pass

def parse_primary(data):
    root = ET.fromstring(data)
    namespaces = {'primary': 'http://linux.duke.edu/metadata/common',
                  'rpm': 'http://linux.duke.edu/metadata/rpm'}

    packages = {}

    for child in root:
        if not child.tag.endswith('}package'):
            continue

        checksum = child.find('primary:checksum', namespaces).text
        name = child.find('primary:name', namespaces).text
        arch = child.find('primary:arch', namespaces).text
        summary = child.find('primary:summary', namespaces).text
        description = child.find('primary:description', namespaces).text
        packager = child.find('primary:packager', namespaces).text
        url = child.find('primary:url', namespaces).text
        time = child.find('primary:time', namespaces)
        file_time = time.attrib['file']
        build_time = time.attrib['build']
        location = child.find('primary:location', namespaces).attrib['href']

        version = child.find('primary:version', namespaces)
        version = {'ver': version.attrib['ver'],
                   'rel': version.attrib['rel'],
                   'epoch': version.attrib['epoch']}

        # format
        fmt = child.find('primary:format', namespaces)

        format_license = fmt.find('rpm:license', namespaces).text
        format_vendor = fmt.find('rpm:vendor', namespaces).text
        format_group = fmt.find('rpm:group', namespaces).text
        format_buildhost = fmt.find('rpm:buildhost', namespaces).text
        format_sourcerpm = fmt.find('rpm:sourcerpm', namespaces).text
        header_range = fmt.find('rpm:header-range', namespaces)
        format_header_start = header_range.attrib['start']
        format_header_end = header_range.attrib['end']

        # provides

        provides = fmt.find('rpm:provides', namespaces)
        if provides is None:
            provides = []

        provides_dict = {}

        for entry in provides:
            provides_name = entry.attrib['name']
            provides_epoch = entry.attrib.get('epoch', None)
            provides_rel = entry.attrib.get('rel', None)
            provides_ver = entry.attrib.get('ver', None)
            provides_flags = entry.attrib.get('flags', None)

            nerv = (provides_name, provides_epoch, provides_rel, provides_ver)

            provides_dict[nerv] = {'name': provides_name,
                                   'epoch': provides_epoch,
                                   'rel': provides_rel,
                                   'ver': provides_ver,
                                   'flags': provides_flags}

        # requires

        requires = fmt.find('rpm:requires', namespaces)
        if requires is None:
            requires = []

        requires_dict = {}

        for entry in requires:
            requires_name = entry.attrib['name']
            requires_epoch = entry.attrib.get('epoch', None)
            requires_rel = entry.attrib.get('rel', None)
            requires_ver = entry.attrib.get('ver', None)
            requires_flags = entry.attrib.get('flags', None)
            requires_pre = entry.attrib.get('pre', None)

            nerv = (requires_name, requires_epoch, requires_rel, requires_ver)

            requires_dict[nerv] = {'name': requires_name,
                                   'epoch': requires_epoch,
                                   'rel': requires_rel,
                                   'ver': requires_ver,
                                   'flags': requires_flags,
                                   'pre': requires_pre}

        # obsoletes

        obsoletes = fmt.find('rpm:obsoletes', namespaces)
        if obsoletes is None:
            obsoletes = []

        obsoletes_dict = {}

        for entry in obsoletes:
            obsoletes_name = entry.attrib['name']
            obsoletes_epoch = entry.attrib.get('epoch', None)
            obsoletes_rel = entry.attrib.get('rel', None)
            obsoletes_ver = entry.attrib.get('ver', None)
            obsoletes_flags = entry.attrib.get('flags', None)

            nerv = (obsoletes_name, obsoletes_epoch, obsoletes_rel, obsoletes_ver)

            obsoletes_dict[nerv] = {'name': obsoletes_name,
                                    'epoch': obsoletes_epoch,
                                    'rel': obsoletes_rel,
                                    'ver': obsoletes_ver,
                                    'flags': obsoletes_flags}

        # files
        files = []
        for node in fmt.findall('primary:file', namespaces):
            file_name = node.text
            file_type = 'file'

            if 'type' in node.attrib and node.attrib['type'] == 'dir':
                file_type = 'dir'
            files.append({'type': file_type, 'name': file_name})


        # result package
        format_dict = {'license': format_license,
                       'vendor': format_vendor,
                       'group': format_group,
                       'buildhost': format_buildhost,
                       'sourcerpm': format_sourcerpm,
                       'header_start': format_header_start,
                       'header_end': format_header_end,
                       'provides': provides_dict,
                       'requires': requires_dict,
                       'obsoletes': obsoletes_dict,
                       'files': files}

        package = {'checksum': checksum, 'name': name, 'arch': arch,
                   'version': version, 'summary': summary,
                   'description': description, 'packager': packager,
                   'url': url, 'file_time': file_time, 'build_time': build_time,
                   'location': location, 'format': format_dict}

        nerv = (name, version['epoch'], version['rel'], version['ver'])
        packages[nerv] = package
    return packages

def parse_ver_str(ver_str):
    if not ver_str:
        return (None, None, None)

    expr = "^(\d+:)?([^-]*)-([^-]*)$"
    match = re.match(expr, ver_str)
    if not match:
        raise RuntimeError("Can't parse version: '%s'" % ver_str)
    epoch = match.group(1)[:-1] if match.group(1) else "0"
    ver = match.group(2)
    rel = match.group(3)
    return (epoch, ver, rel)

def header_to_filelists(header, sha256):
    pkgid = sha256
    name = header['NAME']
    arch = header['ARCH']
    epoch = header.get('EPOCH', None)
    rel = header.get('RELEASE', None)
    ver = header['VERSION']
    version = {'ver': ver, 'rel': rel, 'epoch': epoch}

    dirnames = header['DIRNAMES']

    basenames = header['BASENAMES']
    dirindexes = header['DIRINDEXES']

    files = []
    for entry in zip(basenames, dirindexes):
        filename = entry[0]
        dirname = dirnames[entry[1]]
        files.append({'name': dirname + filename, 'type': 'file'})

    for dirname in dirnames:
        files.append({'name': dirname, 'type': 'dir'})

    package = {'pkgid': pkgid, 'name': name, 'arch': arch,
               'version': version, 'files': files}
    nerv = (name, version['epoch'], version['rel'], version['ver'])

    return nerv, package



def header_to_primary(header, sha256, mtime, location):
    name = header['NAME']
    arch = header['ARCH']
    summary = header['SUMMARY']
    description = header['DESCRIPTION']
    packager = header.get('PACKAGER', None)
    build_time = header['BUILDTIME']
    url = header['URL']
    epoch = header.get('EPOCH', None)
    rel = header.get('RELEASE', None)
    ver = header['VERSION']
    version = {'ver': ver, 'rel': rel, 'epoch': epoch}

    # format

    format_license = header.get('LICENSE', None)
    format_vendor = header.get('VENDOR', None)
    format_group = header.get('GROUP', None)
    format_buildhost = header.get('BUILDHOST', None)
    format_sourcerpm = header.get('SOURCERPM', None)
    format_header_start = None
    format_header_end = None

    # provides

    provides_dict = {}
    providename = header.get('PROVIDENAME', [])
    provideversion = header.get('PROVIDEVERSION', [])
    provideflags = header.get('PROVIDEFLAGS', [])

    for entry in zip(providename, provideversion, provideflags):
        provides_name = entry[0]
        provides_epoch, provides_ver, provides_rel = \
            parse_ver_str(entry[1])
        provides_flags = rpmfile.flags_to_str(entry[2])

        nerv = (provides_name, provides_epoch, provides_rel, provides_ver)

        provides_dict[nerv] = {'name': provides_name,
                               'epoch': provides_epoch,
                               'rel': provides_rel,
                               'ver': provides_ver,
                               'flags': provides_flags}

    # requires

    requires_dict = {}
    requirename = header.get('REQUIRENAME', [])
    requireversion = header.get('REQUIREVERSION', [])
    requireflags = header.get('REQUIREFLAGS', [])

    for entry in zip(requirename, requireversion, requireflags):
        requires_name = entry[0]
        requires_epoch, requires_ver, requires_rel = \
            parse_ver_str(entry[1])
        requires_flags = rpmfile.flags_to_str(entry[2])

        if entry[2] & rpmfile.RPMSENSE_RPMLIB:
            continue

        pre = None

        if entry[2] & 4352:
            pre = "1"

        nerv = (requires_name, requires_epoch, requires_rel, requires_ver)

        requires_dict[nerv] = {'name': requires_name,
                               'epoch': requires_epoch,
                               'rel': requires_rel,
                               'ver': requires_ver,
                               'flags': requires_flags,
                               "pre": pre}

    # obsoletes

    obsoletes_dict = {}
    obsoletename = header.get('OBSOLETENAME', [])
    obsoleteversion = header.get('OBSOLETEVERSION', [])
    obsoleteflags = header.get('OBSOLETEFLAGS', [])

    for entry in zip(obsoletename, obsoleteversion, obsoleteflags):
        obsoletes_name = entry[0]
        obsoletes_epoch, obsoletes_ver, obsoletes_rel = \
            parse_ver_str(entry[1])
        obsoletes_flags = rpmfile.flags_to_str(entry[2])

        nerv = (obsoletes_name, obsoletes_epoch, obsoletes_rel, obsoletes_ver)

        obsoletes_dict[nerv] = {'name': obsoletes_name,
                               'epoch': obsoletes_epoch,
                               'rel': obsoletes_rel,
                               'ver': obsoletes_ver,
                               'flags': obsoletes_flags}

    # files

    dirnames = header['DIRNAMES']

    basenames = header['BASENAMES']
    dirindexes = header['DIRINDEXES']

    files = []
    for entry in zip(basenames, dirindexes):
        filename = entry[0]
        dirname = dirnames[entry[1]]
        files.append({'name': dirname + filename, 'type': 'file'})

    for dirname in dirnames:
        files.append({'name': dirname, 'type': 'dir'})

    # result package
    format_dict = {'license': format_license,
                   'vendor': format_vendor,
                   'group': format_group,
                   'buildhost': format_buildhost,
                   'sourcerpm': format_sourcerpm,
                   'header_start': format_header_start,
                   'header_end': format_header_end,
                   'provides': provides_dict,
                   'requires': requires_dict,
                   'obsoletes': obsoletes_dict,
                   'files': files}

    package = {'checksum': sha256, 'name': name, 'arch': arch,
               'version': version, 'summary': summary,
               'description': description, 'packager': packager,
               'url': url, 'file_time': str(int(mtime)), 'build_time': build_time,
               'location': location, 'format': format_dict}

    nerv = (name, version['epoch'], version['rel'], version['ver'])

    return nerv, package

def update_repo(storage):
    filelists = {}
    primary = {}

    if storage.exists('repodata/repomd.xml'):
        data = storage.read_file('repodata/repomd.xml')

        filelists, primary = parse_repomd(data)

        data = storage.read_file(filelists['location'])
        filelists = parse_filelists(gunzip_string(data))

        data = storage.read_file(primary['location'])
        primary = parse_primary(gunzip_string(data))

    recorded_files = set()
    for package in primary.values():
        recorded_files.add((package['location'], float(package['file_time'])))

    existing_files = set()
    expr = "^.*\.rpm$"
    for file_path in storage.files('.'):
        match = re.match(expr, file_path)

        if not match:
            continue

        mtime = storage.mtime(file_path)

        existing_files.add((file_path, mtime))

    files_to_add = existing_files - recorded_files

    for file_to_add in files_to_add:
        file_path = file_to_add[0]
        mtime = file_to_add[1]
        print("Adding: '%s'" % file_path)


        tmpdir = tempfile.mkdtemp()
        storage.download_file(file_path, os.path.join(tmpdir, 'package.rpm'))

        rpminfo = rpmfile.RpmInfo()
        header = rpminfo.parse_file(os.path.join(tmpdir, 'package.rpm'))
        sha256 = file_checksum(os.path.join(tmpdir, 'package.rpm'), "sha256")

        shutil.rmtree(tmpdir)

        nerv, prim = header_to_primary(header, sha256, mtime, file_path)
        _, flist = header_to_filelists(header, sha256)

        primary[nerv] = prim
        filelists[nerv] = flist


def main():
    stor = storage.FilesystemStorage(sys.argv[1])

    update_repo(stor)

if __name__ == '__main__':
    main()
