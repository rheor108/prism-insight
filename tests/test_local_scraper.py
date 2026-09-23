from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import pytest
from prism_core import local_scraper as scraper


@pytest.mark.asyncio
@pytest.mark.parametrize('url', ['file:///etc/passwd','raw://secret','http://user:pass@example.com','https://example.com:8080'])
async def test_non_public_page_forms_rejected(url):
    with pytest.raises(ValueError): await scraper.public_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize('address',['127.0.0.1','10.0.0.1','169.254.169.254','::1'])
async def test_private_dns_rejected(monkeypatch,address):
    monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',AsyncMock(return_value=[(None,None,None,None,(address,443))]))
    with pytest.raises(ValueError): await scraper.public_url('https://example.com')


def test_tables_and_timestamp_survive_payload():
    md='## 미코\n|연도|매출|\n|---|---|\n|2025|123|'
    result=SimpleNamespace(success=True,status_code=200,markdown=SimpleNamespace(raw_markdown=md),metadata={'title':'미코'})
    payload=scraper.page_payload(result,'https://example.com')
    assert payload['markdown']==md
    assert payload['metadata']['sourceURL']=='https://example.com'
    assert payload['metadata']['retrievedAt']
    assert not payload['metadata']['truncated']


@pytest.mark.parametrize('success,status,body',[(False,200,'Failed'),(True,403,'Access denied'),(True,200,'')])
def test_failed_or_empty_sources_do_not_look_successful(success,status,body):
    result=SimpleNamespace(success=success,status_code=status,markdown=body,metadata={})
    with pytest.raises(ValueError,match='SOURCE_UNAVAILABLE'):
        scraper.page_payload(result,'https://example.com')


def test_large_page_explicitly_marks_truncation():
    result=SimpleNamespace(success=True,status_code=200,markdown='x'*(scraper.MAX_CHARS+1),metadata={})
    payload=scraper.page_payload(result,'https://example.com')
    assert len(payload['markdown'])==scraper.MAX_CHARS and payload['metadata']['truncated']


@pytest.mark.parametrize('final_status', [200, 403])
def test_redirect_checks_final_response(final_status):
    result = SimpleNamespace(success=True, status_code=302,
                             redirected_status_code=final_status,
                             redirected_url='https://example.com/news',
                             markdown='News', metadata={})
    if final_status == 403:
        with pytest.raises(ValueError, match='HTTP 403'):
            scraper.page_payload(result, 'https://example.com')
    else:
        payload = scraper.page_payload(result, 'https://example.com')
        assert payload['metadata']['statusCode'] == 200
        assert payload['metadata']['initialStatusCode'] == 302
        assert payload['metadata']['url'] == 'https://example.com/news'


@pytest.mark.asyncio
async def test_scrape_preserves_contract_without_paid_calls(monkeypatch):
    monkeypatch.setattr(scraper,'public_url',AsyncMock())
    crawl=AsyncMock(return_value={'success':True,'markdown':'Table'})
    monkeypatch.setattr(scraper,'_crawl',crawl)
    assert (await scraper.firecrawl_scrape('https://example.com',formats=['markdown'],maxAge=7200000))['success']
    crawl.assert_awaited_once_with('https://example.com',True,5000,45000)


@pytest.mark.asyncio
async def test_timeout_becomes_explicit_source_failure(monkeypatch):
    monkeypatch.setattr(scraper,'public_url',AsyncMock())
    monkeypatch.setattr(scraper,'_crawl',AsyncMock(side_effect=asyncio.TimeoutError))
    with pytest.raises(ValueError,match='SOURCE_UNAVAILABLE'):
        await scraper.firecrawl_scrape('https://example.com')


@pytest.mark.asyncio
async def test_paid_llm_formats_are_not_supported():
    with pytest.raises(ValueError,match='Only formats'):
        await scraper.firecrawl_scrape('https://example.com',formats=['json'])
