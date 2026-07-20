from pathlib import Path

from django.test import SimpleTestCase


APP_DIR = Path(__file__).resolve().parent


class AIPostAgentIntegrationTests(SimpleTestCase):
    def test_post_form_includes_agent_assets_and_removes_field_feedback_buttons(self):
        template = (APP_DIR / "templates/socialmanager/posts/post_form.html").read_text(encoding="utf-8")
        self.assertIn('_ai_post_agent.html', template)
        self.assertIn('ai_post_agent.js', template)
        self.assertIn('ai_post_agent.css', template)
        self.assertNotIn('data-ai-target=', template)

    def test_agent_is_not_globally_included(self):
        base_template = (APP_DIR / "templates/socialmanager/base.html").read_text(encoding="utf-8")
        self.assertNotIn('_ai_post_agent.html', base_template)
        self.assertNotIn('ai_post_agent.js', base_template)

    def test_agent_uses_backend_url_without_exposing_secrets(self):
        template = (APP_DIR / "templates/socialmanager/posts/_ai_post_agent.html").read_text(encoding="utf-8")
        javascript = (APP_DIR / "static/socialmanager/js/ai_post_agent.js").read_text(encoding="utf-8")
        self.assertIn("post_agent_generate_content", template)
        self.assertIn("fetch(root.dataset.generateUrl", javascript)
        self.assertIn("if (state.isGenerating) return", javascript)
        self.assertNotIn('OPENAI_API_KEY', template + javascript)

    def test_three_step_flow_has_information_goals_and_generation(self):
        template = (APP_DIR / "templates/socialmanager/posts/_ai_post_agent.html").read_text(encoding="utf-8")
        self.assertEqual(template.count("data-progress-step="), 3)
        for step in range(1, 4):
            self.assertIn(f'data-agent-step="{step}"', template)
            self.assertIn(f"Step {step} of 3", template)
        self.assertNotIn("Media and content", template)
        self.assertNotIn("Your post content is ready for AI analysis.", template)
        self.assertIn("Information", template)
        self.assertNotIn("Content Goals", template)
        self.assertNotIn("Content or Project", template)
        self.assertNotIn("Generate content only", template)
        self.assertNotIn("Use a Project", template)
        self.assertNotIn("data-workflow-choice", template)
        self.assertNotIn("data-project-select", template)
        self.assertNotIn("<h3>{% trans \"Media\" %}</h3>", template)

    def test_content_goals_are_one_native_select_with_placeholder_and_stable_values(self):
        template = (APP_DIR / "templates/socialmanager/posts/_ai_post_agent.html").read_text(encoding="utf-8")
        values = (
            "increase_reach", "encourage_engagement", "promote_product_service",
            "build_brand_awareness", "drive_profile_visits", "share_information", "other",
        )
        self.assertEqual(template.count("data-content-goal>"), 1)
        self.assertIn('<option value="" selected disabled hidden>{% trans "Select a content goal" %}</option>', template)
        for value in values:
            self.assertIn(f'value="{value}"', template)
        self.assertLess(template.index('value="share_information"'), template.index('value="other"'))
        self.assertIn("Please select a content goal.", template)

    def test_goal_validation_persistence_and_payload_are_wired(self):
        javascript = (APP_DIR / "static/socialmanager/js/ai_post_agent.js").read_text(encoding="utf-8")
        self.assertIn('contentGoal: ""', javascript)
        self.assertIn('root.querySelector("[data-content-goal]")', javascript)
        self.assertIn("goalError.focus()", javascript)
        self.assertIn('state.contentGoal = event.target.value', javascript)
        self.assertIn('data.set("content_goal", state.contentGoal)', javascript)
        self.assertIn('data.set("custom_content_goal"', javascript)
        self.assertNotIn('data.set("project', javascript)

    def test_other_goal_custom_input_and_skip_removal_are_wired(self):
        template = (APP_DIR / "templates/socialmanager/posts/_ai_post_agent.html").read_text(encoding="utf-8")
        javascript = (APP_DIR / "static/socialmanager/js/ai_post_agent.js").read_text(encoding="utf-8")
        self.assertIn("Custom content goal", template)
        self.assertIn("Describe your content goal...", template)
        self.assertIn('maxlength="150"', template)
        self.assertIn("data-custom-goal-wrap", template)
        self.assertIn('state.contentGoal === "other"', javascript)
        self.assertIn('customWrap.hidden = !isOther', javascript)
        self.assertIn('state.customContentGoal = ""', javascript)
        self.assertNotIn("data-agent-skip", template + javascript)
        self.assertNotIn("confirmSkip", javascript)

    def test_information_is_optional_and_has_updated_description(self):
        template = (APP_DIR / "templates/socialmanager/posts/_ai_post_agent.html").read_text(encoding="utf-8")
        javascript = (APP_DIR / "static/socialmanager/js/ai_post_agent.js").read_text(encoding="utf-8")
        self.assertIn("You may press Next if you do not have an idea yet.", template)
        self.assertIn('maxlength="1000"', template)
        self.assertNotIn("return confirmSkip", javascript)
        self.assertIn('data.set("skipped_context", String(!state.context))', javascript)

    def test_empty_information_uses_nested_confirmation_without_skip(self):
        template = (APP_DIR / "templates/socialmanager/posts/_ai_post_agent.html").read_text(encoding="utf-8")
        javascript = (APP_DIR / "static/socialmanager/js/ai_post_agent.js").read_text(encoding="utf-8")
        self.assertIn("No information added. Continue using the image or video content for AI analysis?", template)
        self.assertIn("Add an image, video, or some information before continuing.", template)
        self.assertIn("informationEmptyConfirmed", javascript)
        self.assertIn("hasAnalyzableVisualMedia", javascript)
        self.assertIn('state.currentStep = 2', javascript)
        self.assertIn("hideModal()", javascript)
        self.assertNotIn("window.confirm", javascript)

    def test_agent_is_true_modal_with_page_lock_focus_trap_and_single_backdrop(self):
        template = (APP_DIR / "templates/socialmanager/posts/_ai_post_agent.html").read_text(encoding="utf-8")
        javascript = (APP_DIR / "static/socialmanager/js/ai_post_agent.js").read_text(encoding="utf-8")
        stylesheet = (APP_DIR / "static/socialmanager/css/components/ai_post_agent.css").read_text(encoding="utf-8")
        self.assertEqual(template.count("data-agent-page-backdrop"), 1)
        self.assertIn('aria-modal="true"', template)
        self.assertIn("appShell.inert = true", javascript)
        self.assertIn("appShell.inert = false", javascript)
        self.assertIn('classList.add("ai-agent-modal-open")', javascript)
        self.assertIn('classList.remove("ai-agent-modal-open")', javascript)
        self.assertIn('document.addEventListener("focusin"', javascript)
        self.assertIn("z-index: 1400", stylesheet)
        self.assertIn("z-index: 1500", stylesheet)

    def test_media_snapshot_deduplicates_files_and_aborts_stale_requests(self):
        javascript = (APP_DIR / "static/socialmanager/js/ai_post_agent.js").read_text(encoding="utf-8")
        self.assertIn("function mediaSignature()", javascript)
        self.assertIn("file.lastModified", javascript)
        self.assertIn("mediaSignature() !== state.mediaSignature", javascript)
        self.assertIn("const seenFiles = new Set()", javascript)
        self.assertIn("if (seenFiles.has(identity)) return", javascript)
        self.assertIn("new AbortController()", javascript)
        self.assertIn("state.abortController?.abort()", javascript)

    def test_custom_goal_uses_shared_input_and_accessible_error_styles(self):
        template = (APP_DIR / "templates/socialmanager/posts/_ai_post_agent.html").read_text(encoding="utf-8")
        stylesheet = (APP_DIR / "static/socialmanager/css/components/ai_post_agent.css").read_text(encoding="utf-8")
        self.assertIn('class="field-input"', template)
        self.assertIn('aria-describedby="ai-agent-custom-goal-count ai-agent-custom-goal-error"', template)
        self.assertIn(".ai-agent-custom-goal .field-input:focus", stylesheet)
        self.assertIn(".ai-agent-custom-goal:has(.ai-agent-error:not([hidden]))", stylesheet)

    def test_launcher_is_image_only_and_reduced_motion_is_supported(self):
        template = (APP_DIR / "templates/socialmanager/posts/_ai_post_agent.html").read_text(encoding="utf-8")
        javascript = (APP_DIR / "static/socialmanager/js/ai_post_agent.js").read_text(encoding="utf-8")
        stylesheet = (APP_DIR / "static/socialmanager/css/components/ai_post_agent.css").read_text(encoding="utf-8")
        launcher = template.split('class="ai-agent-launcher"', 1)[1].split("</button>", 1)[0]
        self.assertIn("Open AI Post Agent", launcher)
        self.assertIn("agent.PNG", launcher)
        self.assertNotIn("<span", launcher)
        self.assertIn("if (!detectMedia())", javascript)
        self.assertIn("prefers-reduced-motion: reduce", stylesheet)

    def test_agent_leaves_project_selection_to_original_form(self):
        form_template = (APP_DIR / "templates/socialmanager/posts/post_form.html").read_text(encoding="utf-8")
        javascript = (APP_DIR / "static/socialmanager/js/ai_post_agent.js").read_text(encoding="utf-8")
        self.assertIn("{{ form.campaign }}", form_template)
        self.assertNotIn('document.getElementById("id_campaign")', javascript)
        self.assertNotIn("workflowChoice", javascript)
        self.assertNotIn("selectedProjectId", javascript)
        self.assertNotIn('data.set("project', javascript)

    def test_agent_header_has_no_context_eyebrow_and_keeps_accessible_controls(self):
        template = (APP_DIR / "templates/socialmanager/posts/_ai_post_agent.html").read_text(encoding="utf-8")
        header = template.split('<header class="ai-agent-header">', 1)[1].split("</header>", 1)[0]
        self.assertIn("Create with Creana", header)
        self.assertNotIn("ai-agent-mode", header)
        self.assertIn("<button", header)
        self.assertIn("Close AI Post Agent", header)
        self.assertIn('aria-expanded="false"', template)

    def test_results_apply_to_active_post_controls_and_keep_form_events(self):
        javascript = (APP_DIR / "static/socialmanager/js/ai_post_agent.js").read_text(encoding="utf-8")
        self.assertIn("function resolvePostField(fieldName)", javascript)
        self.assertIn('document.getElementById(`${prefix}_${suffix}_input`)', javascript)
        self.assertIn('new CustomEvent("create-post:replace-tags"', javascript)
        self.assertIn('new Event("input", { bubbles: true })', javascript)
        self.assertIn('new Event("change", { bubbles: true })', javascript)

    def test_results_are_directly_editable_without_redundant_edit_button(self):
        template = (APP_DIR / "templates/socialmanager/posts/_ai_post_agent.html").read_text(encoding="utf-8")
        javascript = (APP_DIR / "static/socialmanager/js/ai_post_agent.js").read_text(encoding="utf-8")
        self.assertNotIn("data-edit=", template)
        self.assertNotIn('copy("edit")', javascript)
        self.assertIn('document.createElement(name === "title" ? "input" : "textarea")', javascript)

    def test_regenerate_merges_only_requested_fields_and_preserves_edits(self):
        javascript = (APP_DIR / "static/socialmanager/js/ai_post_agent.js").read_text(encoding="utf-8")
        self.assertIn("function syncGeneratedInputsToState()", javascript)
        self.assertIn("function mergeGeneratedContent(existing, incoming, requestedFields)", javascript)
        self.assertIn("state.generatedContent = mergeGeneratedContent", javascript)
        self.assertIn("generateContent([name])", javascript)

    def test_agent_assets_have_new_cache_buster_and_matching_component_styles(self):
        template = (APP_DIR / "templates/socialmanager/posts/post_form.html").read_text(encoding="utf-8")
        stylesheet = (APP_DIR / "static/socialmanager/css/components/ai_post_agent.css").read_text(encoding="utf-8")
        self.assertEqual(template.count("?v=agent-modal-8"), 2)
        self.assertIn(".ai-agent-result-field", stylesheet)
        self.assertIn(".ai-agent-result-field-title", stylesheet)

    def test_success_switches_generation_step_to_results_only_mode(self):
        template = (APP_DIR / "templates/socialmanager/posts/_ai_post_agent.html").read_text(encoding="utf-8")
        javascript = (APP_DIR / "static/socialmanager/js/ai_post_agent.js").read_text(encoding="utf-8")
        stylesheet = (APP_DIR / "static/socialmanager/css/components/ai_post_agent.css").read_text(encoding="utf-8")
        self.assertIn("data-agent-generation-controls", template)
        self.assertIn('root.classList.add("has-generated-results")', javascript)
        self.assertIn(".has-generated-results .ai-agent-generation-controls", stylesheet)
        self.assertIn(".has-generated-results .ai-agent-progress", stylesheet)
        self.assertIn(".has-generated-results .ai-agent-footer", stylesheet)
