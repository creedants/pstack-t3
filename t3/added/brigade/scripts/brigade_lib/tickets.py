"""Patch a name in the _tickets submodule that reads it; patching it here does not reach callers."""
from brigade_lib._tickets.after import Rails, after_cell, after_change, after_holds, read_waits
from brigade_lib._tickets.labels import ActsOn, EVERY_ROW, Fault, GOALS_ONLY, Goal, Label, LabelCounts, Labels, Tally, count_labels, frozen_label, in_flight_tickets, label_words, read_goal, read_label, read_labels, whole_share
from brigade_lib._tickets.goals import add_goal, goal_share, goal_work, open_goal, set_goal
from brigade_lib._tickets.pairs import CAP_WORDS, REVIEW_WORDS, a_mode, cap_pairs, review_pairs, setting_pairs
from brigade_lib._tickets.rail import add_ticket, append_ticket, base_source, finish_request, finished_requests, handed_count, handoff_id, inbox_file, intake_list, is_live, move_ticket, refuse_live_ref, request_files, set_intake, sibling_named, sibling_rails, take_inbox, take_tickets, taken_row, tell
from brigade_lib._tickets.paths import Tracked, pattern_directory, paths_of, priority_of, quoted_paths, record_paths, ticket_paths, tracked_paths, unique_names
from brigade_lib._tickets.follow_ups import FollowUps, NO_WORK, file_follow_ups, follow_up_kind, follow_ups, says_no_work, skipped_follow_up_line
from brigade_lib._tickets.sweep import Assign, Candidate, Drop, Sweep, changes_for, filed_at, sweep_select, sweep_text, sweep_tickets
