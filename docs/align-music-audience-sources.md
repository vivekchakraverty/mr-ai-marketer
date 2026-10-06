# Align Music audience routes

Reviewed 5 October 2026. The Music tab's platform and community routes use a local, fixed
source catalog. That ranking uses the artist's stated goal and release stage; it does not
query those sites, validate a song's genre, contact communities, or predict plays. Style,
location, and reference artists generate research leads, which require manual checking.

After local Essentia analysis, the optional listener research action sends only artist names
entered by the user. MusicBrainz resolves exact artist names to IDs. ListenBrainz provides
aggregate unique-listener counts for those artists and nearby artists from LB Radio. The
result is a set of artist audiences to investigate, not a prediction about the song or any
person. The app does not send the audio or display listener accounts. To discover candidate
soundalikes, the UI links to cosine.club; that step takes place on its site and is strongest
for electronic and underground music.

| Listener source | Use | Primary source |
| --- | --- | --- |
| MusicBrainz | Resolve user-provided artist names to exact artist IDs. | [Artist search API](https://musicbrainz.org/doc/MusicBrainz_API), [rate limit](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting) |
| ListenBrainz | Aggregate listener counts and nearby artists for those IDs. | [Popularity API](https://listenbrainz.readthedocs.io/en/latest/users/api/popularity.html), [LB Radio artist API](https://listenbrainz.readthedocs.io/en/latest/users/api/core.html) |
| cosine.club | Optional manual audio similarity research from a public track link. | [Site FAQ](https://cosine.club/) |

| Route | Verified mechanism | Primary source |
| --- | --- | --- |
| Bandcamp | Artist and release tags feed Search and Discover; location is an artist profile field. | [Bandcamp tag guide](https://get.bandcamp.help/en/articles/15263096-what-are-tags-and-how-do-i-use-them) |
| Bandcamp fan community | Artists can message followers by email and app; comments on community messages require a purchase. | [Bandcamp community guide](https://get.bandcamp.help/en/articles/15263070-who-can-see-my-community-page-and-comment-on-my-messages) |
| SoundCloud | Track genre/tags support finding music; private tracks can be shared by link; listeners can leave timestamped comments. | [Track metadata](https://help.soundcloud.com/hc/en-us/articles/46022345620123-Edit-and-customize-your-tracks), [privacy](https://help.soundcloud.com/hc/en-us/articles/115003451767-Having-a-private-account), [comments](https://help.soundcloud.com/hc/en-us/articles/115003566008-Comments) |
| Audius | Artists can upload music; users can search genres and playlists and engage through comments. | [Artist platform overview](https://blog.audius.co/posts/a-new-era-for-audius), [search](https://help.audius.co/product/advanced-search), [comments](https://help.audius.co/product/comments) |
| YouTube | Song audio needs a video container, such as a visualizer. | [Upload guide](https://support.google.com/youtube/answer/57407?hl=en) |
| YouTube Shorts | An artist can make a Short from a public long-form video, with a link back to the source video. Reach depends on viewer response. | [Shorts from videos](https://support.google.com/youtube/answer/12836917?hl=en-uk), [discovery guidance](https://support.google.com/youtube/answer/11914225?co=YOUTUBE._YTVideoType%3Dshorts&hl=en-GB) |
| Spotify for Artists | Eligible unreleased tracks can be pitched through the official form; editorial consideration is not placement. Already released songs need another route. | [Pitching guide](https://support.spotify.com/st-en/artists/article/pitching-music-and-videos-to-playlist-editors/), [promotion guide](https://support.spotify.com/sm-en/artists/article/promoting-music-on-spotify/) |
| Spotify Artist Pick | An artist can feature a song or playlist on their profile. | [Artist Pick guide](https://support.spotify.com/ml-en/artists/article/managing-your-artist-pick/) |
| r/MusicFeedback | Reciprocal critique is required before posting a song. | [Community bot and rules post](https://www.reddit.com/r/MusicFeedback/comments/1tyo040/musicfeedback_bot_has_been_upgraded_rules_bot_info/) |
| r/IndieMusicFeedback | The community describes a five-comment exchange before submitting a track. | [Community about page](https://indiemusicfeedback.com/about/) |
| r/WeAreTheMusicMakers | Song feedback belongs in a weekly thread, not a new promotional post. | [Weekly feedback thread](https://www.reddit.com/r/WeAreTheMusicMakers/comments/1wxjnwc/rwatmm_weekly_feedback_thread/) |
| r/Songwriting | Work-in-progress feedback and promotional posts use different routes. | [Moderator guidance](https://www.reddit.com/r/Songwriting/comments/o4vv2x), [promotion thread example](https://www.reddit.com/r/Songwriting/comments/1v5ixh9/weekly_selfpromotion_thread/) |
| Scene research | Search can surface a community, but its rules and activity must be checked before sharing music. | [Reddit community guidance](https://www.business.reddit.com/learning-hub/articles/find-subreddits-for-your-business) |

The rules of communities and platform features can change. Recheck each primary page before
changing the app's factual text or release date. Never convert an audio descriptor, a search
result, or a reference artist into an assertion of listener demand. Keep external posting in
the artist's control.
