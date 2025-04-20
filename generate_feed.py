from itertools import compress
from atproto import Client
import json
from tqdm.contrib.concurrent import thread_map
from atproto_client.models.app.bsky.feed.defs import FeedViewPost
from atproto_client.models.app.bsky.richtext.facet import Link
from datetime import datetime, timezone
from pyarxiv import query
import config


def write_json(data, path):
    with open(path, "w") as fp:
        json.dump(data, fp, indent=4)


def parse_date(date_string):
    return datetime.fromisoformat(date_string.replace("Z", "+00:00"))


def is_ml_preprint(arxiv_url: str):

    def _get_arxiv_category(arxiv_id: str):
        try:
            entries = query(querystring=f"id:{arxiv_id}")
        except Exception as err:
            print(f"Error for {arxiv_url} and {arxiv_id}")
            return None
        if not entries:
            print(f"Missing arxiv id: {arxiv_id}")
            return None
        entry = entries[0]
        return entry["arxiv_primary_category"]["term"]

    # Reference: https://arxiv.org/category_taxonomy
    # "cs.LG"
    ALLOWED_CATEGORIES = ["cs.AI", "cs.CL", "cs.CV", "cs.MA"]
    arxiv_id = (
        arxiv_url.split("/")[-1]
        .replace(".pdf", "")
        .split("#")[0]
        .split("v")[0]
        .split("?")[0]
    )
    primary_category = _get_arxiv_category(arxiv_id)
    return primary_category in ALLOWED_CATEGORIES


def parse_arxiv_urls(item: FeedViewPost):
    if item.post.record.facets:
        urls = [
            feature.uri
            for facet in item.post.record.facets
            for feature in facet.features
            if type(feature) is Link
        ]
        arxiv_urls = [uri for uri in urls if "arxiv.org" in uri]
        return arxiv_urls


def hackernews_score(item, gravity: float = 2.5):
    hours_passed = (
        datetime.now(timezone.utc) - parse_date(item.post.indexed_at)
    ).total_seconds() / 3600
    if hours_passed >= 12:
        return 0
    else:
        points = (
            item.post.like_count
            + item.post.quote_count
            + item.post.reply_count
            + item.post.repost_count
        )
        score = points / ((hours_passed + 2) ** (gravity))
        return score


def rank_posts(feed):
    # return sorted(feed, key=lambda item: parse_date(item.post.indexed_at), reverse=True)
    return sorted(feed, key=hackernews_score, reverse=True)


def filter_item(item: FeedViewPost) -> bool:
    if item.post.author.handle.startswith(
        (
            "arxiv-cs-",
            "arxiv-stat-",
            "paperposterbot.bsky.social",
            "optb0t.bsky.social",
            "ericzzj.bsky.social",
            "hackernewstop5.bsky.social",
            "hnws.bsky.social",
            "paper.bsky.social",
            "betterhn50.e-work.xyz",
            "hnbot.gsuscs.xyz",
            "hn100.bsky.social",
            "ministryofai.bsky.social",
        )
    ):
        return False

    arxiv_urls = parse_arxiv_urls(item)
    if arxiv_urls:
        for arxiv_url in arxiv_urls:
            if is_ml_preprint(arxiv_url):
                return True
        return False
    else:
        post_text = item.post.record.text
        return any(
            keyword.lower() in post_text.lower()
            for keyword in [
                "aclweb.org",
                "aclanthology.org",
            ]
        )


def fetch_latest_posts():
    client = Client()
    client.login(config.HANDLE, config.PASSWORD)

    data = client.app.bsky.feed.get_feed(
        {
            "feed": config.SKYFEED_PATH,
            "limit": 100,
        },
        timeout=100,
    )

    feed = data.feed
    for _ in range(2):
        data = client.app.bsky.feed.get_feed(
            {"feed": config.SKYFEED_PATH, "limit": 100, "cursor": data.cursor},
            timeout=200,
        )
        feed.extend(data.feed)

    bool_filter = thread_map(filter_item, feed)
    filtered_feed = compress(feed, bool_filter)
    sorted_feed = rank_posts(filtered_feed)
    post_uris = [item.post.uri for item in sorted_feed]
    return post_uris


def main():
    did_data = {
        "@context": ["https://www.w3.org/ns/did/v1"],
        "id": f"did:web:{config.SERVICE_DOMAIN}",
        "service": [
            {
                "id": "#bsky_fg",
                "type": "BskyFeedGenerator",
                "serviceEndpoint": f"https://{config.SERVICE_DOMAIN}",
            }
        ],
    }
    write_json(did_data, "./_site/.well-known/did.json")

    feed_generator_data = {
        "encoding": "application/json",
        "body": {"did": config.SERVICE_DID, "feeds": [{"uri": config.FEED_URI}]},
    }

    write_json(feed_generator_data, "./_site/xrpc/app.bsky.feed.describeFeedGenerator")

    # Fetch latest posts and prepare data in the format expected by Bluesky protocol
    post_uris = fetch_latest_posts()

    feed_skeletion = {"feed": [{"post": uri} for uri in post_uris]}
    write_json(feed_skeletion, "./_site/xrpc/app.bsky.feed.getFeedSkeleton")


if __name__ == "__main__":
    main()
