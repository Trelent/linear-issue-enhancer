"""Background task for /retry command."""

import tempfile

from src.linear import add_comment, get_issue, update_issue_description
from src.sync import sync_all_async
from src.commands.shared import (
    DOCS_DIR,
    ENHANCEMENT_MARKER,
    _extract_original_description,
    _build_enhancement_markers,
    research_context,
    research_codebase,
    write_retry_description,
)


async def retry_enhance_issue(
    issue_id: str,
    feedback: str,
    model_shorthand: str | None = None,
    reply_to_id: str | None = None,
):
    """Retry enhancing an issue based on user feedback.
    
    Args:
        issue_id: The issue ID
        feedback: User's feedback on the previous enhancement
        model_shorthand: Optional model selection
        reply_to_id: Optional comment ID to reply to (for threading)
    """
    print(f"\n{'='*60}", flush=True)
    print(f"🔄 Retrying enhancement for issue: {issue_id}", flush=True)
    print(f"   Model: {model_shorthand or 'default'}", flush=True)
    if reply_to_id:
        print(f"   Reply to: {reply_to_id}", flush=True)
    print(f"{'='*60}\n", flush=True)
    
    # Add "working on it" comment immediately
    try:
        await add_comment(issue_id, "🔄 _Retrying enhancement with your feedback..._", parent_id=reply_to_id)
    except Exception as e:
        if "Entity not found" in str(e) or "not found" in str(e).lower():
            print(f"⚠️ Issue {issue_id} no longer exists, skipping retry", flush=True)
            return
        raise
    
    # Fetch current issue data
    try:
        issue = await get_issue(issue_id)
    except Exception as e:
        print(f"❌ Failed to fetch issue: {e}", flush=True)
        await add_comment(issue_id, "❌ _Failed to fetch issue data. Please check server logs for details._", parent_id=reply_to_id)
        return
    
    current_description = issue.description or ""
    title = issue.title
    
    # Extract original description from marker
    original_description = _extract_original_description(current_description)
    if original_description is None:
        print("⚠️ No original description marker found, treating as first enhancement", flush=True)
        original_description = ""
    
    # Strip markers from current description to get the AI-written part
    ai_description = current_description
    if ENHANCEMENT_MARKER in ai_description:
        ai_description = ai_description.split(ENHANCEMENT_MARKER)[0].strip()
    
    print(f"   Title: {title}", flush=True)
    print(f"   Original: {len(original_description)} chars", flush=True)
    print(f"   AI version: {len(ai_description)} chars", flush=True)
    print(f"   Feedback: {feedback[:100]}..." if len(feedback) > 100 else f"   Feedback: {feedback}", flush=True)
    
    try:
        prompt = f"Issue: {title}"
        if original_description:
            prompt += f"\n\nOriginal notes:\n{original_description}"
        
        print("📥 Syncing data sources...", flush=True)
        await sync_all_async(DOCS_DIR)
        
        print("🔬 Step 1: Researching context (Slack/GDrive)...", flush=True)
        try:
            context = await research_context(prompt, model_shorthand)
        except Exception as e:
            print(f"⚠️ Context research error: {e}", flush=True)
            context = f"Error researching context: {e}"
        
        print("🔬 Step 2: Researching codebase (with context)...", flush=True)
        with tempfile.TemporaryDirectory() as work_dir:
            try:
                code_analysis = await research_codebase(prompt, context, work_dir, model_shorthand)
            except Exception as e:
                print(f"⚠️ Code research error: {e}", flush=True)
                code_analysis = f"Error researching code: {e}"
        
        print("✍️ Writing enhanced description (with feedback)...", flush=True)
        enhanced = await write_retry_description(
            title, original_description, ai_description, feedback, context, code_analysis, model_shorthand
        )
        
        markers = _build_enhancement_markers(original_description)
        enhanced_with_marker = f"{enhanced}\n\n{markers}"
        
        print(f"📝 Updating Linear issue...", flush=True)
        success = await update_issue_description(issue_id, enhanced_with_marker)
        
        if success:
            print(f"✅ Issue re-enhanced successfully!", flush=True)
            await add_comment(issue_id, "_✅ Issue re-enhanced based on your feedback._", parent_id=reply_to_id)
        else:
            print(f"❌ Failed to update issue via Linear API", flush=True)
            await add_comment(issue_id, "⚠️ _Failed to update issue description. Please check the logs._", parent_id=reply_to_id)
            
    except Exception as e:
        print(f"❌ Retry enhancement failed with error: {e}", flush=True)
        import traceback
        traceback.print_exc()
        await add_comment(issue_id, "❌ _Retry enhancement failed during issue processing. Please check server logs for details._", parent_id=reply_to_id)
