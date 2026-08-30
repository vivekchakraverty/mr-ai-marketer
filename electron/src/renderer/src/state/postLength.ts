/**
 * How long a post counts as, per network.
 *
 * Bluesky's own limit is 300 *graphemes*, and its API accepts up to 3000 UTF-8 bytes
 * alongside that. The Activepieces connector that actually publishes for us does not
 * implement that rule. Read out of the running container, `piece-bluesky` 0.1.7 does:
 *
 *     let T = new RichText({ text: A })
 *     await T.detectFacets(P)
 *     if (T.length > 300) throw new Error("Post text cannot exceed 300 characters")
 *
 * `RichText.length` in `@atproto/api` is the UTF-8 **byte** length, not the grapheme count
 * — that is `graphemeLength`. So the connector measures bytes against a limit that is
 * supposed to be graphemes, and every character outside ASCII costs two or three against
 * a budget the user is told is 300 characters.
 *
 * The failure this caused looked impossible from the outside: a post of exactly 300
 * characters, rejected for exceeding 300 characters. Its three typographic apostrophes
 * (U+2019, three bytes each) made it 306 bytes.
 *
 * So Bluesky is measured in UTF-8 bytes here. It is stricter than Bluesky really is, and
 * deliberately so: it is exactly what the connector will accept, and a count that promises
 * more than the send path allows is worse than one that is merely conservative. Every other
 * network is counted in characters, which is what those connectors do.
 *
 * If the connector is ever fixed, or Bluesky posting moves to a native transport the way
 * Mastodon media did, this becomes `[...text].length` like the rest and the limit stops
 * being a lie in the other direction.
 */

const encoder = new TextEncoder()

export function postLength(text: string, channel: string): number {
  return channel === 'bluesky' ? encoder.encode(text).length : text.length
}

/** What the counter should call the unit, so an over-budget post explains itself. */
export function postLengthUnit(channel: string): string {
  return channel === 'bluesky' ? 'bytes' : 'characters'
}
