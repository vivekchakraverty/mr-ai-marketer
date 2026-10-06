import type { EssentiaSongResult } from './essentiaTypes'

export type MusicGoal = 'feedback' | 'listeners' | 'sales' | 'playlists'
export type ReleaseStage = 'draft' | 'unreleased' | 'released'

export interface MusicAudienceContext {
  style: string
  referenceArtists: string
  location: string
  goal: MusicGoal
  stage: ReleaseStage
}

export interface MusicDestination {
  id: string
  kind: 'Platform' | 'Community' | 'Research lead'
  name: string
  priority: number
  reason: string
  nextSteps: string[]
  boundary: string
  sourceName: string
  sourceUrl: string
  secondSource?: { name: string; url: string }
  visitUrl: string
}

export const RESEARCH_REVIEWED = '5 October 2026'

function redditSearch(query: string): string {
  return `https://www.reddit.com/search/?q=${encodeURIComponent(query)}&type=communities`
}

function feedbackQuestion(audio: EssentiaSongResult): string {
  if (audio.sections.length < 2) return 'Ask for one specific production or songwriting critique.'
  const loudest = audio.sections.reduce((best, section) => section.levelDbfs > best.levelDbfs ? section : best)
  const quietest = audio.sections.reduce((best, section) => section.levelDbfs < best.levelDbfs ? section : best)
  const spread = loudest.levelDbfs - quietest.levelDbfs
  if (spread < 3) return 'Ask whether the arrangement sustains interest across the full track; level alone cannot answer that.'
  const time = Math.floor(loudest.startSeconds / 60) + ':' + String(Math.floor(loudest.startSeconds % 60)).padStart(2, '0')
  return `Ask whether the lift near ${time} feels earned. Essentia measured about ${spread.toFixed(1)} dB of section-level range, but cannot judge the musical transition.`
}

export function suggestMusicDestinations(audio: EssentiaSongResult, context: MusicAudienceContext): MusicDestination[] {
  const style = context.style.trim().slice(0, 120)
  const referenceArtists = context.referenceArtists.trim().slice(0, 120)
  const location = context.location.trim().slice(0, 100)
  const feedback = context.goal === 'feedback'
  const draft = context.stage === 'draft'
  const unreleased = context.stage === 'unreleased'
  const genreCue = style ? `Use your own “${style}” description in the metadata; Essentia did not infer that style.` : 'Add a style or subgenre you can defend by listening; tempo and spectral balance do not establish genre.'
  const critique = feedbackQuestion(audio)
  const items: MusicDestination[] = [
    {
      id: 'soundcloud', kind: 'Platform', name: 'SoundCloud', priority: feedback ? 96 : 76,
      reason: feedback ? 'A private track link and timestamped comments make this a practical route for listening notes before release.' : 'Public tracks use genre and tags for discovery, and listeners can comment at specific moments.',
      nextSteps: [draft ? 'Upload privately and share the secret link only with people whose feedback you want.' : 'Set an accurate genre and a small set of sound-specific tags.', critique],
      boundary: 'A comment feature is a way to collect reactions, not evidence that this audience will like the song.',
      sourceName: 'SoundCloud track tags', sourceUrl: 'https://help.soundcloud.com/hc/en-us/articles/46022345620123-Edit-and-customize-your-tracks',
      secondSource: { name: 'Timestamped comments', url: 'https://help.soundcloud.com/hc/en-us/articles/115003566008-Comments' }, visitUrl: 'https://soundcloud.com/upload'
    },
    {
      id: 'bandcamp', kind: 'Platform', name: 'Bandcamp', priority: context.goal === 'sales' ? 98 : context.goal === 'listeners' ? 83 : 56,
      reason: 'Bandcamp uses release and artist tags in Search and Discover; that gives a listener a route to find a well-tagged release.',
      nextSteps: [genreCue, location ? `Set your actual location to ${location}; Bandcamp also uses artist location in discovery.` : 'Add your real artist location if a local scene matters.', 'Review existing tag suggestions before choosing a primary genre tag.'],
      boundary: draft ? 'Best considered once the track is ready to publish, rather than as a private feedback room.' : 'Tag relevance can improve discoverability; it does not predict sales.',
      sourceName: 'Bandcamp tagging guide', sourceUrl: 'https://get.bandcamp.help/en/articles/15263096-what-are-tags-and-how-do-i-use-them', visitUrl: 'https://bandcamp.com/discover'
    },
    {
      id: 'audius', kind: 'Platform', name: 'Audius', priority: context.goal === 'listeners' ? 78 : context.goal === 'sales' ? 80 : 60,
      reason: 'Audius supports direct artist uploads, genre search, playlists, comments, and direct fan interaction.',
      nextSteps: [style ? `Search Audius for ${style} and inspect active tracks and playlists before uploading.` : 'Search your strongest genre description and inspect active playlists.', 'Follow and engage with artists or curators whose catalog genuinely overlaps yours.'],
      boundary: 'Check current rights and upload terms. Playlist or fan response is not guaranteed.',
      sourceName: 'Audius search guide', sourceUrl: 'https://help.audius.co/product/advanced-search',
      secondSource: { name: 'Artist uploads and community', url: 'https://blog.audius.co/posts/a-new-era-for-audius' }, visitUrl: 'https://audius.co/'
    },
    {
      id: 'youtube', kind: 'Platform', name: 'YouTube', priority: context.goal === 'listeners' ? 82 : 65,
      reason: 'A visualizer, lyric video, performance, or other video can make the song accessible through search and sharing.',
      nextSteps: ['Pair the audio with artwork or video; YouTube does not accept an audio-only file as a video upload.', 'Write a truthful title and description using the style and story you can support.', 'Listen to the loudest measured section before choosing any short preview; loudest does not necessarily mean best hook.'],
      boundary: 'This is a video route. Essentia cannot decide which moment will retain viewers.',
      sourceName: 'YouTube upload guide', sourceUrl: 'https://support.google.com/youtube/answer/57407?hl=en', visitUrl: 'https://studio.youtube.com/'
    },
    {
      id: 'musicfeedback', kind: 'Community', name: 'r/MusicFeedback', priority: feedback ? 93 : 67,
      reason: 'This community has an explicit give-before-you-post feedback system for individual tracks.',
      nextSteps: ['Give two substantive critiques on recent songs before posting.', critique, 'Ask for a specific reaction, then respond to people who took time to listen.'],
      boundary: 'Its June 2026 bot rules require qualifying feedback comments; read the live rules before posting. Do not place your link in another artist’s comments.',
      sourceName: 'Community bot rules', sourceUrl: 'https://www.reddit.com/r/MusicFeedback/comments/1tyo040/musicfeedback_bot_has_been_upgraded_rules_bot_info/', visitUrl: 'https://www.reddit.com/r/MusicFeedback/'
    },
    {
      id: 'indiemusicfeedback', kind: 'Community', name: 'r/IndieMusicFeedback', priority: feedback ? 90 : 64,
      reason: 'An independent-music feedback community built around reciprocal listening.',
      nextSteps: ['Read and comment constructively on five other posts before sharing a track.', 'Frame your request around one decision you can still change, such as arrangement, mix, or positioning.'],
      boundary: 'The community describes a five-comment requirement. Check its current rules and bot instructions before posting.',
      sourceName: 'Community about page', sourceUrl: 'https://indiemusicfeedback.com/about/', visitUrl: 'https://www.reddit.com/r/IndieMusicFeedback/'
    },
    {
      id: 'watmm', kind: 'Community', name: 'r/WeAreTheMusicMakers feedback thread', priority: feedback ? 88 : 59,
      reason: 'The weekly thread is a defined place to request critique from other music makers.',
      nextSteps: ['Find the current weekly feedback thread, rather than posting a new feedback topic.', 'Share one song, give at least three constructive comments, and state what you want assessed.', critique],
      boundary: 'The thread says it is for feedback, not promotional posts; it is replaced weekly. Follow the current thread’s rules.',
      sourceName: 'Weekly thread rules', sourceUrl: 'https://www.reddit.com/r/WeAreTheMusicMakers/comments/1wxjnwc/rwatmm_weekly_feedback_thread/', visitUrl: 'https://www.reddit.com/r/WeAreTheMusicMakers/search/?q=weekly%20feedback%20thread&restrict_sr=1&sort=new'
    },
    {
      id: 'reddit_scene', kind: 'Research lead', name: 'Find a style-specific Reddit scene', priority: style ? 77 : 35,
      reason: style ? `“${style}” is artist-supplied context. A focused community search can locate people discussing that sound.` : 'A style-specific community might be useful once you can describe the song’s sound accurately.',
      nextSteps: [style ? `Search for communities around ${style}, then read recent posts to see whether original tracks are discussed.` : 'Enter a defensible style above to generate a focused community search.', 'Read each community’s current promotion rules. Participate in the discussion before asking people to hear your track.'],
      boundary: 'A search result is a lead, not an approved place to post or a prediction of audience fit.',
      sourceName: 'Reddit community guidance', sourceUrl: 'https://www.business.reddit.com/learning-hub/articles/find-subreddits-for-your-business', visitUrl: redditSearch(style ? `${style} music` : 'independent music feedback')
    }
  ]

  if (unreleased) items.push({
    id: 'spotify_pitch', kind: 'Platform', name: 'Spotify for Artists editorial pitch', priority: context.goal === 'playlists' ? 99 : 71,
    reason: 'Spotify provides an official editorial pitch for eligible upcoming, unreleased songs delivered by a distributor or label.',
    nextSteps: ['Confirm the release appears under Music → Upcoming in Spotify for Artists.', 'Submit one specific pitch before release, with accurate genre, mood, story, and promotion details.', 'If possible, deliver and pitch at least seven days before release for follower Release Radar eligibility.'],
    boundary: 'An editorial pitch does not guarantee a playlist placement. A released track cannot be newly pitched through this route.',
    sourceName: 'Spotify pitching guide', sourceUrl: 'https://support.spotify.com/st-en/artists/article/pitching-music-and-videos-to-playlist-editors/', visitUrl: 'https://artists.spotify.com/'
  })

  if (context.stage === 'released') items.push({
    id: 'spotify_followers', kind: 'Platform', name: 'Spotify for Artists: grow existing listeners', priority: context.goal === 'playlists' ? 82 : 63,
    reason: 'For an already released song, Spotify for Artists offers profile, sharing, and audience tools. The editorial pitch route is for eligible unreleased songs.',
    nextSteps: ['Check where listeners already come from in Spotify for Artists.', 'Ask interested listeners to follow the artist profile and save or share the song if they choose.', 'Use real audience response to refine future release metadata and pitches.'],
    boundary: 'A released song cannot be newly submitted through Spotify’s unreleased-song editorial pitch form. Avoid services promising guaranteed streams or placements.',
    sourceName: 'Spotify promotion guide', sourceUrl: 'https://support.spotify.com/sm-en/artists/article/promoting-music-on-spotify/',
    secondSource: { name: 'Editorial pitch eligibility', url: 'https://support.spotify.com/st-en/artists/article/pitching-music-and-videos-to-playlist-editors/' }, visitUrl: 'https://artists.spotify.com/'
  })

  if (context.stage === 'released') items.push({
    id: 'spotify_artist_pick', kind: 'Platform', name: 'Spotify Artist Pick', priority: context.goal === 'listeners' ? 75 : 61,
    reason: 'Artist Pick lets an artist feature a released song or playlist at the top of their Spotify profile.',
    nextSteps: ['Open your artist profile in Spotify for Artists and feature the song or a relevant artist playlist.', 'Add a short, honest message explaining why a visitor should start there.', 'Revisit the Pick when your release focus changes.'],
    boundary: 'This helps people who visit your profile. It is not a playlist submission or a promise of additional reach.',
    sourceName: 'Spotify Artist Pick guide', sourceUrl: 'https://support.spotify.com/ml-en/artists/article/managing-your-artist-pick/', visitUrl: 'https://artists.spotify.com/'
  })

  if (context.stage === 'released') items.push({
    id: 'youtube_shorts', kind: 'Platform', name: 'YouTube Shorts from your video', priority: context.goal === 'listeners' ? 79 : 60,
    reason: 'YouTube lets you turn a public long-form video into a Short that links back to the original video.',
    nextSteps: ['Publish a full visualizer or performance video first.', 'Use its Remix → Edit into a Short action to select a moment you believe works on its own.', 'Compare engaged views and comments with other clips before repeating a format.'],
    boundary: 'Availability depends on the source video and account. The loudest measured passage is not necessarily the best short clip.',
    sourceName: 'Shorts from videos guide', sourceUrl: 'https://support.google.com/youtube/answer/12836917?hl=en-uk',
    secondSource: { name: 'Shorts discovery guidance', url: 'https://support.google.com/youtube/answer/11914225?co=YOUTUBE._YTVideoType%3Dshorts&hl=en-GB' }, visitUrl: 'https://studio.youtube.com/'
  })

  if (context.stage === 'released') items.push({
    id: 'bandcamp_community', kind: 'Community', name: 'Your Bandcamp fan community', priority: context.goal === 'sales' ? 86 : 58,
    reason: 'Bandcamp has a community page where an artist can send messages to followers through email and the app.',
    nextSteps: ['Share the release with context that existing followers would appreciate.', 'Invite a specific response or question, then reply to comments.', 'Use what those fans say as evidence for your next audience test.'],
    boundary: 'This reaches an existing fan relationship. It is not a way to reach unrelated listeners, and comments require a purchase.',
    sourceName: 'Bandcamp community guide', sourceUrl: 'https://get.bandcamp.help/en/articles/15263070-who-can-see-my-community-page-and-comment-on-my-messages', visitUrl: 'https://bandcamp.com/'
  })

  if (draft) items.push({
    id: 'songwriting', kind: 'Community', name: 'r/Songwriting work-in-progress feedback', priority: feedback ? 84 : 55,
    reason: 'For an original, unreleased work in progress, this community has a feedback route centered on songwriting craft.',
    nextSteps: ['State which lyric, melody, or structure decision is still open.', 'Share only if the song meets the current work-in-progress rules; use the promotion thread once released.'],
    boundary: 'Released songs and promotional links belong in its designated promotion thread, not a work-in-progress feedback post.',
    sourceName: 'Community posting guidance', sourceUrl: 'https://www.reddit.com/r/Songwriting/comments/o4vv2x', visitUrl: 'https://www.reddit.com/r/Songwriting/'
  })

  if (location) items.push({
    id: 'local_scene', kind: 'Research lead', name: `Explore the ${location} music scene`, priority: 66,
    reason: 'A real location can narrow the search to local artists, events, and listening communities; it does not by itself establish a match.',
    nextSteps: [`Search ${location}${style ? ` + ${style}` : ''} communities and inspect recent activity.`, 'Look for venues, collectives, radio shows, and open calls that explicitly accept this kind of music.', 'Contact people with a specific reason their audience might care; do not send mass pitches.'],
    boundary: 'No local organization has been verified or contacted by this app.',
    sourceName: 'Reddit community guidance', sourceUrl: 'https://www.business.reddit.com/learning-hub/articles/find-subreddits-for-your-business', visitUrl: redditSearch(`${location} ${style} music`.trim())
  })

  if (referenceArtists) items.push({
    id: 'reference_research', kind: 'Research lead', name: 'Map adjacent artists and curators', priority: 72,
    reason: `You named ${referenceArtists} as a reference. That is a useful search starting point, not a verified sonic match.`,
    nextSteps: ['Inspect where those artists are tagged, playlisted, and discussed.', 'Identify curators or communities that repeatedly engage with nearby artists, then listen to their recent selections.', 'Pitch only when your track fits their stated submission route.'],
    boundary: 'This route does not verify that a curator or community accepts music like yours. Check their current work before contacting them.',
    sourceName: 'Bandcamp tagging guide', sourceUrl: 'https://get.bandcamp.help/en/articles/15263096-what-are-tags-and-how-do-i-use-them', visitUrl: `https://bandcamp.com/search?q=${encodeURIComponent(referenceArtists)}`
  })

  return items.sort((left, right) => right.priority - left.priority || left.name.localeCompare(right.name))
}
