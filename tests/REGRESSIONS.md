# Regressions

Every bug that reached a person, and the test that keeps it fixed. A new bug adds a line here.

| Bug | Test |
|---|---|
| Spoken "oh, oh, oh" with pauses taken for music and removed | `tests/test_speech.py::test_voiced_calls_with_pauses_are_a_voice_even_if_the_vad_never_hears_them` |
| A long sound the voice model heard once taken for music | `tests/test_speech.py::test_a_long_sound_the_vad_heard_even_once_is_not_music` |
| Quiet residue after denoising hid how voiced a stretch is | `tests/test_speech.py::test_voiced_calls_with_pauses_are_a_voice_even_if_the_vad_never_hears_them` |
| Web export labels all said `removed` | `tests/test_speech_module.py::test_web_export_labels_name_what_was_removed_like_the_command_line` |
| Playhead did not land on the click (snapped to a fix box's centre) | `web/src/components/Timeline.test.tsx`, `web/e2e/main.spec.ts` (playback) |
| Overview click went to the window's centre, not the click | `web/src/components/project/Minimap.test.tsx`, `web/e2e/main.spec.ts` (playback) |
| Overview window pulled back by playback | `web/e2e/main.spec.ts` (playback) |
| Nothing tested real playback through source switches and seeks | `web/e2e/main.spec.ts` (playback) |
| Output format choice shown when there was nothing to decide | `web/src/components/project/ExportDialog.test.tsx` |
| Export choice in two places acted as one radio group | `web/src/pages/ProjectPage.test.tsx` (export window) |
| Float vs same bit depth: only float cancels in a null test | `tests/test_io.py::test_null_test_against_the_original_float_cancels_and_same_depth_does_not` |
| Esc in the folder picker closed the export window too | `web/src/components/project/ExportDialog.test.tsx` |
| Browser test left behind when the export window changed | `tests/check_all.py` runs it every time |
| Tests could export into the real Documents folder | `tests/server/test_settings_api.py`, `web/e2e/main.spec.ts` |
| No time left shown while a GPU step reports no progress | `web/src/progress.test.ts`, `tests/server/test_api.py::test_a_busy_project_carries_step_speeds_so_the_time_left_shows_from_the_start` |
| Re-processing could keep showing the old waveform | `web/src/components/Timeline.test.tsx`, `tests/server/test_api.py::test_every_run_has_a_new_rev_so_no_cache_can_serve_the_old_result` |
| Work data followed the folder the server was started from | `tests/server/test_settings_api.py::test_apply_location_prefers_the_environment_then_the_saved_choice` |
| Work folder inside the system temp folder went unnoticed | `tests/server/test_settings_api.py::test_settings_warn_about_folders_inside_the_system_temp_folder` |
| Restart from Settings had to pick up the new folder | `tests/server/test_launcher.py::test_restart_from_settings_comes_back_on_the_folder_saved_for_the_next_start` |
| Export scrubbing rewrote a test's example path and broke the public test suite | `tests/test_export_public.py::test_export_changes_no_source_file` |
| `requires-python` allowed versions the pinned numpy has no wheels for | `tests/test_export_public.py::test_python_range_stops_where_the_pinned_numpy_has_wheels` |
| GPU image build failed: pip 24.0 in the base image rejects `--progress-bar raw` | `tests/test_gpu_install.py::test_pip_commands_ask_for_raw_progress_only_where_pip_has_it` |
| Glitch module test compared computed samples bit-exactly and failed intermittently on macOS arm64 (last-bit differences, max 5.1e-15) | `tests/test_glitch_module.py::test_glitch_repair_equals_algorithm_and_only_touches_fix_regions` |
