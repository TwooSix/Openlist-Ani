"""Find sequels, prequels, and related works for an anime."""

from openlist_ani.config import config
from openlist_ani.core.bangumi.client import BangumiClient


async def run(
    subject_id: str = "",
    **kwargs,
) -> str:
    """Fetch related subjects (sequels, prequels, side stories, etc.).

    Args:
        subject_id: Bangumi subject ID (required).
    """
    if not subject_id:
        return "Error: 'subject_id' parameter is required."

    client = BangumiClient(access_token=config.bangumi_token)
    try:
        items = await client.fetch_related_subjects(int(subject_id))
    except Exception as e:
        return f"Error fetching related subjects: {e}"
    finally:
        await client.close()

    if not items:
        return f"No related subjects found for subject {subject_id}."

    lines = [f"Related subjects for ID:{subject_id} ({len(items)} found):\n"]
    for item in items:
        subj = item.subject
        name = subj.name_cn or subj.name
        if subj.name_cn and subj.name:
            name = f"{subj.name_cn} ({subj.name})"
        relation = item.relation
        lines.append(f"  - [{relation}] [ID:{subj.id}] {name}")

    return "\n".join(lines)
