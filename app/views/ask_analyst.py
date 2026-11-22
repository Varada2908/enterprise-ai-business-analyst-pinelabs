"""Ask the Analyst: natural-language questions answered by the Text-to-SQL pipeline (quick answers) or the
multi-step investigator (deep investigations), with conversation memory for follow-ups."""
from __future__ import annotations

from html import escape

import streamlit as st

from components.answers import render_turn
from components.layout import page_header
from services import analyst
from services.backend import api_key_configured, default_model

page_header("Analytics", "Ask the Analyst",
            "Ask in plain English. Answers come with the SQL, the evidence behind them and caveats.")

if "conversation" not in st.session_state:
    st.session_state["conversation"] = analyst.Conversation()
convo: analyst.Conversation = st.session_state["conversation"]
st.session_state.setdefault("ask_mode", "auto")


def ask(question: str, mode: str = "auto") -> None:
    """Queue a question (used by buttons); it runs on the next script run."""
    st.session_state["queued"] = (question, mode)


def new_conversation() -> None:
    st.session_state["conversation"] = analyst.Conversation()
    st.session_state.pop("queued", None)


def submit() -> None:
    """Send button / Ctrl+Enter: queue the typed question with the selected mode, then clear the box."""
    text = (st.session_state.get("ask_text") or "").strip()
    if not text:
        st.session_state["ask_empty"] = True
        return
    st.session_state["queued"] = (text, st.session_state.get("ask_mode") or "auto")
    st.session_state["ask_text"] = ""


def composer() -> None:
    with st.container(key="composer"):
        with st.form("ask_form", border=False):
            st.text_area("Your question", key="ask_text", height=76, label_visibility="collapsed",
                         placeholder="Ask anything about the payments data, e.g. 'Which merchants had the most "
                                     "failed UPI payments in October?' or 'Why did Wallet success rate dip?'",
                         disabled=not has_key)
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.segmented_control("Mode", options=list(analyst.MODE_LABELS), format_func=analyst.MODE_LABELS.get,
                                     key="ask_mode", label_visibility="collapsed", required=True,
                                     help=" · ".join(f"{analyst.MODE_LABELS[m]}: {h}"
                                                     for m, h in analyst.MODE_HELP.items()))
                st.caption("Ctrl+Enter to send · follow-ups use the conversation so far")
                st.space("stretch")
                st.form_submit_button("Send", key="ask_send", type="primary", icon=":material/send:",
                                      on_click=submit, disabled=not has_key)
    if st.session_state.pop("ask_empty", False):
        st.toast("Type a question first.", icon=":material/edit:")


def suggestions() -> None:
    st.html('<div class="section-title small">Or start from an example</div>')
    cols = st.columns(3, gap="small")
    for i, (kind, q) in enumerate(analyst.SUGGESTED):
        with cols[i % 3]:
            with st.container(border=True, key=f"suggest_{i}"):
                tag = "Deep investigation" if kind == "deep" else "Quick answer"
                st.html(f'<span class="tag tag-{kind}">{tag}</span><div class="sq">{escape(q)}</div>')
                st.button("Ask this", key=f"sqbtn_{i}", icon=":material/arrow_forward:", type="tertiary",
                          on_click=ask, args=(q, kind), disabled=not has_key)


# ----------------------------------------------------------------------------- header actions
has_key = api_key_configured()
if convo.turns:
    with st.container(horizontal=True, horizontal_alignment="right", key="ask_toolbar"):
        st.button("New conversation", icon=":material/add_comment:", type="tertiary", on_click=new_conversation)

if not has_key:
    st.warning("No Gemini API key is configured, so questions can't be answered. Add `GEMINI_API_KEY` to "
               "`ai_analyst/.env` and restart the app. The Overview page works without it.", icon=":material/key_off:")

# a question handed over from the Overview page's incident cards
pending = st.session_state.pop("pending_question", None)
if pending and has_key:
    st.session_state["queued"] = (pending, "deep")
question, mode = st.session_state.pop("queued", (None, None))

# ----------------------------------------------------------------------------- history
if not convo.turns and not question:
    st.html('<div class="hero"><div class="hero-title">What would you like to know?</div>'
            '<div class="hero-sub">Type any question about payments: success rates, TPV, failures, merchants, '
            'banks, devices. Quick answers take seconds; deep investigations run several validated queries '
            'and take 1–3 minutes.</div></div>')

for i, turn in enumerate(convo.turns):
    render_turn(turn, key=str(i), is_last=(i == len(convo.turns) - 1 and not question), ask=ask)

# ----------------------------------------------------------------------------- run a new question
if question and has_key:
    kind, reason = analyst.route(question, mode or "auto", convo.last_kind)
    model = st.session_state.get("model") or default_model()
    rounds = int(st.session_state.get("max_rounds", 4))
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(question.replace("$", r"\$"))
    with st.chat_message("assistant", avatar=":material/insights:"):
        label = ("Investigating: planning queries, checking evidence…" if kind == "deep"
                 else "Writing and validating SQL…")
        with st.status(label, expanded=True) as status:
            st.caption(f"{reason} · {model}" + (f" · up to {rounds} rounds" if kind == "deep" else ""))
            ticker = st.empty()

            def tick(elapsed: float) -> None:
                hint = " (deep investigations usually take 1–3 minutes)" if kind == "deep" else ""
                ticker.markdown(f"Elapsed **{elapsed:.0f} s**{hint}")

            ok = convo.ask(question, kind, reason, model, rounds, on_tick=tick).success
            status.update(label="Done" if ok else "Finished with a problem", state="complete" if ok else "error",
                          expanded=False)
    st.rerun()

# ----------------------------------------------------------------------------- composer (always visible)
composer()
if not convo.turns:
    suggestions()
