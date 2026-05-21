"""
Regression tests for tool-card persistence on session reload.

The older loadSession() path rewrote message history on the client:
- dropped role='tool' rows
- dropped empty assistant rows even when they carried tool_calls
- then ignored session.tool_calls on reload

That broke both durable logging and page refresh for valid tool runs.
"""
import json
import pathlib
import subprocess
import textwrap

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()
def _run_js(script_body: str) -> dict:
    script = textwrap.dedent(f"""
        function loadSessionShape(messages, sessionToolCalls) {{
            const filtered = (messages || []).filter(m => m && m.role);
            const hasMessageToolMetadata = filtered.some(m => {{
                if (!m || m.role !== 'assistant') return false;
                const hasTc = Array.isArray(m.tool_calls) && m.tool_calls.length > 0;
                const hasTu = Array.isArray(m.content) && m.content.some(p => p && p.type === 'tool_use');
                return hasTc || hasTu;
            }});
            const toolCalls = (!hasMessageToolMetadata && sessionToolCalls && sessionToolCalls.length)
                ? sessionToolCalls.map(tc => ({{ ...tc, done: true }}))
                : [];
            return {{ filtered, hasMessageToolMetadata, toolCalls }};
        }}

        {script_body}
    """)
    proc = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(proc.stdout)


def test_reload_keeps_empty_assistant_toolcall_anchor():
    """OpenAI-style assistant {content:'', tool_calls:[...]} must survive reload."""
    result = _run_js("""
        const messages = [
            { role: 'user', content: 'list files' },
            {
                role: 'assistant',
                content: '',
                tool_calls: [{ id: 'call-1', function: { name: 'terminal', arguments: '{}' } }]
            },
            { role: 'tool', tool_call_id: 'call-1', content: '{"output":"ok"}' },
            { role: 'assistant', content: 'Done.' }
        ];
        const loaded = loadSessionShape(messages, [{ name: 'terminal', assistant_msg_idx: 1 }]);
        process.stdout.write(JSON.stringify({
            filtered_len: loaded.filtered.length,
            has_metadata: loaded.hasMessageToolMetadata,
            fallback_len: loaded.toolCalls.length,
            assistant_tool_idx: loaded.filtered.findIndex(m => m.role === 'assistant' && m.tool_calls),
            tool_idx: loaded.filtered.findIndex(m => m.role === 'tool')
        }));
    """)
    assert result["filtered_len"] == 4
    assert result["has_metadata"] is True
    assert result["fallback_len"] == 0
    assert result["assistant_tool_idx"] == 1
    assert result["tool_idx"] == 2


def test_reload_uses_session_summary_when_messages_have_no_tool_metadata():
    """Older sessions should still render from session.tool_calls on reload."""
    result = _run_js("""
        const messages = [
            { role: 'user', content: 'build site' },
            { role: 'assistant', content: 'Starting.' },
            { role: 'tool', content: '{"bytes_written": 4955}' },
            { role: 'assistant', content: '' }
        ];
        const sessionToolCalls = [
            { name: 'write_file', assistant_msg_idx: 1, snippet: 'bytes_written', tid: '' }
        ];
        const loaded = loadSessionShape(messages, sessionToolCalls);
        process.stdout.write(JSON.stringify({
            has_metadata: loaded.hasMessageToolMetadata,
            fallback_len: loaded.toolCalls.length,
            done_flag: loaded.toolCalls[0] && loaded.toolCalls[0].done === true
        }));
    """)
    assert result["has_metadata"] is False
    assert result["fallback_len"] == 1
    assert result["done_flag"] is True
