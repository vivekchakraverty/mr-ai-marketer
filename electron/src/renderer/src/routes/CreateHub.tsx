import { useAppStore } from '../state/store'
import ToolCard from '../components/ToolCard'

export default function CreateHub(): React.JSX.Element {
  const openTool = useAppStore((s) => s.openTool)

  return (
    <div style={{ position: 'relative', maxWidth: 1120, margin: '0 auto', padding: '30px 34px 60px' }}>
      <div
        style={{
          position: 'absolute',
          top: 20,
          right: 30,
          width: 36,
          height: 36,
          background: 'var(--tool-guest)',
          clipPath: 'polygon(50% 0%, 61% 35%, 98% 35%, 68% 57%, 79% 91%, 50% 70%, 21% 91%, 32% 57%, 2% 35%, 39% 35%)',
          animation: 'sway 3s ease-in-out infinite'
        }}
      />
      <div style={{ marginBottom: 24 }}>
        <div
          style={{
            font: "700 13.5px 'Quicksand'",
            letterSpacing: '.05em',
            textTransform: 'uppercase',
            color: 'var(--ink-faint)',
            textDecoration: 'underline wavy',
            textDecorationColor: 'var(--ink-faint)',
            textUnderlineOffset: 4
          }}
        >
          Create
        </div>
        <div style={{ font: "700 33px 'Kalam'", color: 'var(--ink)', marginTop: 6 }}>What are we making today?</div>
        <div style={{ font: "600 14.5px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>
          Pick a generator. Everything lands in your Library, ready to unleash on the world.
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 18 }}>
        <ToolCard
          size="hub"
          index="01"
          icon="blog"
          title="Blog Writer"
          description="Word-vomit a keyword in, get something people actually enjoy reading out."
          onClick={() => openTool('blog')}
        />
        <ToolCard
          size="hub"
          index="02"
          icon="guest"
          title="Guest Post Suggester"
          description="Strangers on the internet willing to publish your genius, ranked by clout."
          onClick={() => openTool('guest')}
        />
        <ToolCard
          size="hub"
          index="03"
          icon="tutorial"
          title="Tutorial Maker"
          description="Step-by-step guides so foolproof even you could follow them."
          onClick={() => openTool('tutorial')}
        />
        <ToolCard
          size="hub"
          index="04"
          icon="docu"
          title="DocuMaker"
          description="Docs so clean your support team might get a day off."
          onClick={() => openTool('docu')}
        />
        <ToolCard
          size="hub"
          index="05"
          icon="social"
          title="Bluesky Post Creator"
          description="Studies what actually got engagement in your niche, then writes posts that rhyme with it."
          onClick={() => openTool('social')}
        />
        <ToolCard
          size="hub"
          index="06"
          icon="mastodon"
          title="Mastodon Post Creator"
          description="Reads your server's house rules to you first, then writes something that won't get you suspended."
          onClick={() => openTool('mastodon')}
        />
        <ToolCard
          size="hub"
          index="07"
          icon="tumblr"
          title="Tumblr Post Creator"
          description="Learns from posts that actually earned their notes, tags included."
          onClick={() => openTool('tumblr')}
        />
        <ToolCard
          size="hub"
          index="08"
          icon="email"
          title="Email Writer"
          description="Trained on real marketing emails, so it sounds like a copywriter, not a chatbot."
          onClick={() => openTool('email')}
        />
      </div>
    </div>
  )
}
