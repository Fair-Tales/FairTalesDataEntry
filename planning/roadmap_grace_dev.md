# Roadmap – Grace Dev

_Original document written by Grace at the end of her summer development work.
Converted from Word document to Markdown for version control.
Each item has been cross-referenced with GitHub issues — see planning/issue_priority.md._

---

## Feature ideas and outstanding work

- **Remember me log in button** → GitHub issue #49
- **Back button** (browser back button messes up session states) → GitHub issue #49
- **Link characters to books in database** — can edit characters when editing book (characters nested within book collection). What happens when a character appears in multiple books? Solution: store list of references to characters, so they can appear in multiple books. → GitHub issue #50
- **Only add aliases to characters in specific book** → GitHub issue #50
- **Delete characters and aliases functionality** → GitHub issue #50
- **More clear photo instructions** — naming scheme? example? re-order/re-name page photos → GitHub issue #51
- **Integrate Vertex AI** so that autodetection of characters/transcript can be used to speed up inputting → _Superseded: using Claude or OpenAI instead_ → GitHub issue #52
- **Firebase admin conflict** — Firebase Admin SDK wants one version of Google Cloud; Google AI SDK wants another → GitHub issue #52
- **For any document assign unique ID** → partially addressed (email = user doc ID; title slug = book doc ID)
- **More reduction in traffic with Firestore** (login page done — was rerunning initialisation). Caching book data — periodic updates (last saved message?) → GitHub issue #53
