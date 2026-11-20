from __future__ import annotations

import unittest

from muse.contextual_inquiry import (
    ActionExperiment,
    ActionFeedback,
    ContextualInquiry,
    FeedbackOutcome,
    Hypothesis,
    Observation,
    SafetyRoute,
    SourceKind,
    StakeholderView,
    ViewStatus,
)


class ContextualInquiryContractTests(unittest.TestCase):
    def observation(self, observation_id="obs-1", kind=SourceKind.USER_QUOTE):
        return Observation(
            observation_id=observation_id,
            source_kind=kind,
            content="The meeting ended earlier than planned.",
            source_ref="conversation:turn-1",
        )

    def experiment(self):
        return ActionExperiment(
            experiment_id="exp-1",
            owner="requester",
            due_date="2026-11-25",
            resource_constraints=("15 minutes",),
            expected_signal="A follow-up conversation is scheduled.",
            stop_condition="Stop if the other participant declines or distress increases.",
        )

    def test_observation_keeps_user_quote_provenance_explicit(self):
        item = self.observation()
        self.assertEqual(item.source_kind, SourceKind.USER_QUOTE)
        self.assertEqual(item.source_ref, "conversation:turn-1")

    def test_hypothesis_requires_sources_or_explicit_low_confidence_label(self):
        with self.assertRaises(ValueError):
            Hypothesis("h-1", "A motive caused the event.", ())
        item = Hypothesis(
            "h-1", "One possible explanation is time pressure.", (),
            confidence=0.2, unsourced=True, falsification_condition="A schedule record contradicts this.",
        )
        self.assertTrue(item.unsourced)
        with self.assertRaises(ValueError):
            Hypothesis(
                "h-2", "A certain motive caused the event.", (),
                confidence=0.9, unsourced=True, falsification_condition="New evidence appears.",
            )

    def test_competing_hypotheses_can_coexist_and_include_counter_evidence(self):
        observation = self.observation()
        counter_observation = Observation(
            "obs-2", SourceKind.OBSERVABLE_FACT, "Two agenda items remained open.", "meeting-notes:1",
        )
        first = Hypothesis(
            "h-1", "The meeting ended due to time pressure.", ("obs-1",),
            confidence=0.45, falsification_condition="The schedule shows no time constraint.",
        )
        second = Hypothesis(
            "h-2", "The meeting ended because the agenda was complete.", ("obs-1",),
            counter_refs=("obs-2",), confidence=0.3,
            falsification_condition="Participants report unfinished agenda items.",
        )
        inquiry = ContextualInquiry(
            "case-1", "Understand why the meeting ended.",
            (observation, counter_observation), (first, second),
        )
        self.assertEqual(len(inquiry.hypotheses), 2)

    def test_unknown_third_party_perspective_is_not_filled_in(self):
        view = StakeholderView("participant-b", ViewStatus.UNKNOWN)
        inquiry = ContextualInquiry(
            "case-2", "Understand a disagreement.", stakeholder_views=(view,),
        )
        self.assertEqual(inquiry.stakeholder_views[0].statement, "")

    def test_expressed_stakeholder_view_requires_its_own_statement_source(self):
        observation = self.observation(kind=SourceKind.OBSERVABLE_FACT)
        view = StakeholderView("participant-b", ViewStatus.EXPRESSED, "I disagree.", "obs-1")
        with self.assertRaises(ValueError):
            ContextualInquiry("case-3", "Understand a disagreement.", (observation,), stakeholder_views=(view,))

    def test_action_experiment_must_be_reversible_and_observable(self):
        with self.assertRaises(ValueError):
            ActionExperiment(
                "exp-bad", "requester", "2026-11-25", (), "Signal", "Stop", reversible=False,
            )
        with self.assertRaises(ValueError):
            ActionExperiment(
                "exp-bad", "requester", "not-a-date", (), "Signal", "Stop",
            )

    def test_non_normal_safety_route_blocks_ordinary_action_experiments(self):
        with self.assertRaises(ValueError):
            ContextualInquiry(
                "case-4", "A high-risk situation.",
                action_experiments=(self.experiment(),),
                safety_route=SafetyRoute.URGENT_SUPPORT,
            )

    def test_feedback_can_record_disconfirming_results_without_auto_promoting_claims(self):
        feedback = ActionFeedback(
            "exp-1", FeedbackOutcome.ADVERSE, "The conversation increased tension.",
            side_effects="Both participants requested a pause.",
            contradicted_hypothesis_ids=("h-1",),
        )
        self.assertEqual(feedback.contradicted_hypothesis_ids, ("h-1",))
        self.assertEqual(feedback.outcome, FeedbackOutcome.ADVERSE)

    def test_persistence_requires_explicit_consent(self):
        inquiry = ContextualInquiry("case-5", "Reflect on a difficult interaction.")
        with self.assertRaises(PermissionError):
            inquiry.assert_persistable()
        consented = ContextualInquiry(
            "case-6", "Reflect on a difficult interaction.", consent_to_store=True,
        )
        consented.assert_persistable()

    def test_unknown_observation_references_are_rejected(self):
        hypothesis = Hypothesis(
            "h-1", "A possible explanation.", ("missing-observation",),
            falsification_condition="A contradictory record is found.",
        )
        with self.assertRaises(ValueError):
            ContextualInquiry("case-7", "Understand the event.", hypotheses=(hypothesis,))

    def test_unknowns_and_resource_constraints_accept_descriptive_text(self):
        inquiry = ContextualInquiry(
            "case-8", "Clarify an incomplete situation.",
            unknowns=("Whether the schedule changed after the meeting was first arranged.",),
            action_experiments=(self.experiment(),),
        )
        self.assertIn("Whether the schedule changed", inquiry.unknowns[0])


if __name__ == "__main__":
    unittest.main()
