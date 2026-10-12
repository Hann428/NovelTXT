from novel_app.batch import parse_urls


def test_chat_and_markdown_batch_inputs_deduplicate_book_links():
    text='第一本：[小说](https://m.41nr.com/book/28425/)；第二本：https://example.org/book/2。\nhttps://m.41nr.com/book/28425#top'
    assert parse_urls(text)==['https://m.41nr.com/book/28425/','https://example.org/book/2']


def test_verification_query_removed_but_regular_query_preserved():
    assert parse_urls('https://m.41nr.com/book/28425/?page=2&__cf_chl_rt_tk=temporary#top')==['https://m.41nr.com/book/28425/?page=2']


def test_consecutive_urls_and_parentheses_in_path():
    assert parse_urls('https://example.org/a,https://example.org/b;https://example.org/c_(d)')==[
        'https://example.org/a','https://example.org/b','https://example.org/c_(d)']
