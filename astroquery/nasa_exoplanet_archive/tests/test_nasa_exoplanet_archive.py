# Licensed under a 3-clause BSD style license - see LICENSE.rst


import json
import os
import sys
from urllib.parse import urlencode

import astropy.units as u
from astropy.io.ascii import write as ap_write
from astroquery.utils.commons import parse_coordinates
from astropy.table import Table as AstroTable
import numpy as np
import pkg_resources
import pytest
import requests
try:
    pyvo_OK = True
    from pyvo.auth import authsession
except ImportError:
    pyvo_OK = False
    pytest.skip("Install pyvo for the nasa_exoplanet_archive module.", allow_module_level=True)
from astropy.coordinates import SkyCoord
from astropy.tests.helper import assert_quantity_allclose
from astropy.utils.exceptions import AstropyDeprecationWarning

from ...exceptions import InputWarning, InvalidQueryError, NoResultsWarning
from ...utils.testing_tools import MockResponse
from ..core import NasaExoplanetArchive, conf, InvalidTableError
try:
    from unittest.mock import Mock, patch, PropertyMock
except ImportError:
    pytest.skip("Install mock for the nasa_exoplanet_archive tests.", allow_module_level=True)

MAIN_DATA = pkg_resources.resource_filename("astroquery.nasa_exoplanet_archive", "data")
TEST_DATA = pkg_resources.resource_filename(__name__, "data")
RESPONSE_FILE = os.path.join(TEST_DATA, "responses.json")

# API accessible tables will gradually transition to TAP service
# Check https://exoplanetarchive.ipac.caltech.edu/docs/TAP/usingTAP.html for an up-to-date list
API_TABLES = [
    ("cumulative", dict(where="kepid=10601284")),
    ("koi", dict(where="kepid=10601284")),
    ("q1_q17_dr25_sup_koi", dict(where="kepid=10601284")),
    ("q1_q17_dr25_koi", dict(where="kepid=10601284")),
    ("q1_q17_dr24_koi", dict(where="kepid=10601284")),
    ("q1_q16_koi", dict(where="kepid=10601284")),
    ("q1_q12_koi", dict(where="kepid=10601284")),
    ("q1_q8_koi", dict(where="kepid=10601284")),
    ("q1_q6_koi", dict(where="kepid=10601284")),
    ("tce", dict(where="kepid=10601284")),
    ("q1_q17_dr25_tce", dict(where="kepid=10601284")),
    ("q1_q17_dr24_tce", dict(where="kepid=10601284")),
    ("q1_q16_tce", dict(where="kepid=10601284")),
    ("q1_q12_tce", dict(where="kepid=10601284")),
    ("keplerstellar", dict(where="kepid=10601284")),
    ("q1_q17_dr25_supp_stellar", dict(where="kepid=10601284")),
    ("q1_q17_dr25_stellar", dict(where="kepid=10601284")),
    ("q1_q17_dr24_stellar", dict(where="kepid=10601284")),
    ("q1_q16_stellar", dict(where="kepid=10601284")),
    ("q1_q12_stellar", dict(where="kepid=10601284")),
    ("keplertimeseries", dict(kepid=8561063, quarter=14)),
    ("kelttimeseries", dict(where="kelt_sourceid='KELT_N02_lc_012738_V01_east'", kelt_field="N02")),
    ("kelt", dict(where="kelt_sourceid='KELT_N02_lc_012738_V01_east'", kelt_field="N02")),
    ("superwasptimeseries", dict(sourceid="1SWASP J191645.46+474912.3")),
    ("k2targets", dict(where="epic_number=206027655")),
    ("k2candidates", dict(where="epic_name='EPIC 206027655'")),
    ("missionstars", dict(where="star_name='tau Cet'")),
    ("mission_exocat", dict(where="star_name='HIP 5110 A'")),
    pytest.param(
        "toi",
        dict(where="toi=256.01"),
        marks=pytest.mark.skipif(
            sys.platform.startswith("win"), reason="TOI table cannot be loaded on Windows"
        ),
    ),
]


def mock_get(self, method, url, *args, **kwargs):  # pragma: nocover
    assert url == conf.url_api

    params = kwargs.get("params", None)
    assert params is not None

    try:
        with open(RESPONSE_FILE, "r") as f:
            responses = json.load(f)
    except FileNotFoundError:
        responses = {}

    # Work out where the expected response is saved
    table = params["table"]
    key = urlencode(sorted(params.items()))
    try:
        index = responses.get(table, []).index(key)
    except ValueError:
        index = -1

    # If the NASA_EXOPLANET_ARCHIVE_GENERATE_RESPONSES environment variable is set, we make a
    # remote request if necessary. Otherwise we throw a ValueError.
    if index < 0:
        if "NASA_EXOPLANET_ARCHIVE_GENERATE_RESPONSES" not in os.environ:
            raise ValueError("unexpected request")
        with requests.Session() as session:
            resp = session.old_request(method, url, params=params)
        responses[table] = responses.get(table, [])
        responses[table].append(key)
        index = len(responses[table]) - 1
        with open(os.path.join(TEST_DATA, "{0}_expect_{1}.txt".format(table, index)), "w") as f:
            f.write(resp.text)
        with open(RESPONSE_FILE, "w") as f:
            json.dump(responses, f, sort_keys=True, indent=2)

    with open(os.path.join(TEST_DATA, "{0}_expect_{1}.txt".format(table, index)), "r") as f:
        data = f.read()

    return MockResponse(data.encode("utf-8"))


@pytest.fixture
def patch_get(request):  # pragma: nocover
    try:
        mp = request.getfixturevalue("monkeypatch")
    except AttributeError:
        mp = request.getfuncargvalue("monkeypatch")

    # Keep track of the original function so that we can use it to generate the expected responses
    requests.Session.old_request = requests.Session.request
    mp.setattr(requests.Session, "request", mock_get)
    return mp


def test_regularize_object_name(patch_get):
    NasaExoplanetArchive._tap_tables = ['list']
    assert NasaExoplanetArchive._regularize_object_name("kepler 2") == "HAT-P-7"
    assert NasaExoplanetArchive._regularize_object_name("kepler 1 b") == "TrES-2 b"

    with pytest.warns(NoResultsWarning) as warning:
        NasaExoplanetArchive._regularize_object_name("not a planet")
    assert "No aliases found for name: 'not a planet'" == str(warning[0].message)


def test_backwards_compat(patch_get):
    """
    These are the tests from the previous version of this interface.
    They query old tables by default and should return InvalidTableError.
    """
    NasaExoplanetArchive._tap_tables = ['list']

    # test_hd209458b_exoplanets_archive
    with pytest.warns(AstropyDeprecationWarning):
        with pytest.raises(InvalidTableError) as error:
            params = NasaExoplanetArchive.query_planet("HD 209458 b ")
        assert "replaced" in str(error)

    # test_hd209458b_exoplanet_archive_coords
    with pytest.warns(AstropyDeprecationWarning):
        with pytest.raises(InvalidTableError) as error:
            params = NasaExoplanetArchive.query_planet("HD 209458 b ")
        assert "replaced" in str(error)

    # test_hd209458_stellar_exoplanet
    with pytest.warns(AstropyDeprecationWarning):
        with pytest.raises(InvalidTableError) as error:
            params = NasaExoplanetArchive.query_star("HD 209458")
        assert "replaced" in str(error)

    # test_hd136352_stellar_exoplanet_archive
    with pytest.warns(AstropyDeprecationWarning):
        with pytest.raises(InvalidTableError) as error:
            params = NasaExoplanetArchive.query_star("HD 136352")
        assert "replaced" in str(error)

    # test_exoplanet_archive_query_all_columns
    with pytest.warns(AstropyDeprecationWarning):
        with pytest.raises(InvalidTableError) as error:
            params = NasaExoplanetArchive.query_planet("HD 209458 b ", all_columns=True)
        assert "replaced" in str(error)


@pytest.mark.filterwarnings("error")
@pytest.mark.parametrize("table,query", API_TABLES)
def test_api_tables(patch_get, table, query):
    NasaExoplanetArchive._tap_tables = ['list']
    data = NasaExoplanetArchive.query_criteria(table, select="*", **query)
    assert len(data) > 0

    # Check that the units were fixed properly
    for col in data.columns:
        assert isinstance(data[col], SkyCoord) or not isinstance(data[col].unit, u.UnrecognizedUnit)


# Mock tests on TAP service below
@patch('astroquery.nasa_exoplanet_archive.core.get_access_url',
       Mock(side_effect=lambda x: 'https://some.url'))
@pytest.mark.skipif(not pyvo_OK, reason='not pyvo_OK')
def test_query_object():
    nasa_exoplanet_archive = NasaExoplanetArchive()

    def mock_run_query(object_name="K2-18 b", table="pscomppars", select="pl_name,disc_year,discoverymethod,ra,dec"):
        assert object_name == "K2-18 b"
        assert table == "pscomppars"
        assert select == "pl_name,disc_year,discoverymethod,ra,dec"
        result = PropertyMock()
        result = {'pl_name': 'K2-18 b', 'disc_year': 2015, 'discoverymethod': 'Transit', 'ra': [172.560141] * u.deg, 'dec': [7.5878315] * u.deg}

        return result
    nasa_exoplanet_archive.query_object = mock_run_query
    response = nasa_exoplanet_archive.query_object()
    assert response['pl_name'] == 'K2-18 b'
    assert response['disc_year'] == 2015
    assert 'Transit' in response['discoverymethod']
    assert response['ra'] == [172.560141] * u.deg
    assert response['dec'] == [7.5878315] * u.deg


@patch('astroquery.nasa_exoplanet_archive.core.get_access_url',
       Mock(side_effect=lambda x: 'https://some.url'))
@pytest.mark.skipif(not pyvo_OK, reason='not pyvo_OK')
def test_query_region():
    nasa_exoplanet_archive = NasaExoplanetArchive()

    def mock_run_query(table="ps", select='pl_name,ra,dec', coordinates=SkyCoord(ra=172.56 * u.deg, dec=7.59 * u.deg), radius=1.0 * u.deg):
        assert table == "ps"
        assert select == 'pl_name,ra,dec'
        assert radius == 1.0 * u.deg
        result = PropertyMock()
        result = {'pl_name': 'K2-18 b'}
        return result
    nasa_exoplanet_archive.query_region = mock_run_query
    response = nasa_exoplanet_archive.query_region()
    assert 'K2-18 b' in response['pl_name']


@patch('astroquery.nasa_exoplanet_archive.core.get_access_url',
       Mock(side_effect=lambda x: 'https://some.url'))
@pytest.mark.skipif(not pyvo_OK, reason='not pyvo_OK')
def test_query_criteria():
    nasa_exoplanet_archive = NasaExoplanetArchive()

    def mock_run_query(table="ps", select='pl_name,discoverymethod,dec', where="discoverymethod like 'Microlensing' and dec > 0"):
        assert table == "ps"
        assert select == "pl_name,discoverymethod,dec"
        assert where == "discoverymethod like 'Microlensing' and dec > 0"
        result = PropertyMock()
        result = {'pl_name': 'TCP J05074264+2447555 b', 'discoverymethod': 'Microlensing', 'dec': [24.7987499] * u.deg}
        return result
    nasa_exoplanet_archive.query_criteria = mock_run_query
    response = nasa_exoplanet_archive.query_criteria()
    assert 'TCP J05074264+2447555 b' in response['pl_name']
    assert 'Microlensing' in response['discoverymethod']
    assert response['dec'] == [24.7987499] * u.deg


@patch('astroquery.nasa_exoplanet_archive.core.get_access_url',
       Mock(side_effect=lambda x: 'https://some.url'))
@pytest.mark.skipif(not pyvo_OK, reason='not pyvo_OK')
def test_get_query_payload():
    nasa_exoplanet_archive = NasaExoplanetArchive()

    def mock_run_query(table="ps", get_query_payload=True, select="count(*)", where="disc_facility like '%TESS%'"):
        assert table == "ps"
        assert get_query_payload
        assert select == "count(*)"
        assert where == "disc_facility like '%TESS%'"
        result = PropertyMock()
        result = {'table': 'ps', 'select': 'count(*)', 'where': "disc_facility like '%TESS%'", 'format': 'ipac'}
        return result
    nasa_exoplanet_archive.query_criteria = mock_run_query
    response = nasa_exoplanet_archive.query_criteria()
    assert 'ps' in response['table']
    assert 'count(*)' in response['select']
    assert "disc_facility like '%TESS%'" in response['where']


@patch('astroquery.nasa_exoplanet_archive.core.get_access_url',
       Mock(side_effect=lambda x: 'https://some.url'))
@pytest.mark.skipif(not pyvo_OK, reason='not pyvo_OK')
def test_select():
    nasa_exoplanet_archive = NasaExoplanetArchive()

    def mock_run_query(table="ps", select=["hostname", "pl_name"], where="hostname='Kepler-11'", get_query_payload=True):
        assert table == "ps"
        assert select == ["hostname", "pl_name"]
        assert where == "hostname='Kepler-11'"
        assert get_query_payload
        payload = PropertyMock()
        payload = {'table': 'ps', 'select': 'hostname,pl_name', 'where': "hostname='Kepler-11'", 'format': 'ipac'}
        return payload
    nasa_exoplanet_archive.query_criteria = mock_run_query
    payload = nasa_exoplanet_archive.query_criteria()
    assert payload["select"] == "hostname,pl_name"


@patch('astroquery.nasa_exoplanet_archive.core.get_access_url',
       Mock(side_effect=lambda x: 'https://some.url'))
@pytest.mark.skipif(not pyvo_OK, reason='not pyvo_OK')
def test_get_tap_tables():
    nasa_exoplanet_archive = NasaExoplanetArchive()

    def mock_run_query(url=conf.url_tap):
        assert url == conf.url_tap
        result = PropertyMock()
        result = ['transitspec', 'emissionspec', 'ps', 'pscomppars', 'keplernames', 'k2names']
        return result
    nasa_exoplanet_archive.get_tap_tables = mock_run_query
    result = nasa_exoplanet_archive.get_tap_tables()
    assert 'ps' in result
    assert 'pscomppars' in result
