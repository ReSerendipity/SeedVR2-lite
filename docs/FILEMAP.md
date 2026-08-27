# FILEMAP.md — SeedVR2-lite 文件结构清单

> 本文件由 `scripts/update_docs.py` 维护末尾 AUTO-SYNC 标记；
> 目录结构描述以实际仓库树为准（自动生成，噪声/构建/资产大文件已过滤）。

## 顶层

| 类型 | 条目 |
|---|---|
| 目录 | `app/` |
| 目录 | `assets/` |
| 目录 | `build/` |
| 目录 | `common/` |
| 目录 | `configs_3b/` |
| 目录 | `configs_7b/` |
| 目录 | `data/` |
| 目录 | `demo/` |
| 目录 | `dist/` |
| 目录 | `docs/` |
| 目录 | `dogfood-output/` |
| 目录 | `examples/` |
| 目录 | `launcher/` |
| 目录 | `logs/` |
| 目录 | `model/` |
| 目录 | `model_lib/` |
| 目录 | `outputs/` |
| 目录 | `perf/` |
| 目录 | `scripts/` |
| 目录 | `tests/` |
| 目录 | `training/` |
| 目录 | `website/` |
| 文件 | `AGENTS.md` |
| 文件 | `CHANGELOG.md` |
| 文件 | `CODE_OF_CONDUCT.md` |
| 文件 | `CONTRIBUTING.md` |
| 文件 | `Dockerfile` |
| 文件 | `LICENSE` |
| 文件 | `LOCAL_RULES.md` |
| 文件 | `NOTICE` |
| 文件 | `README.md` |
| 文件 | `SECURITY.md` |
| 文件 | `USER_AGREEMENT.md` |
| 文件 | `config.yaml` |
| 文件 | `config.yaml.bak.20260826` |
| 文件 | `install.bat` |
| 文件 | `install.sh` |
| 文件 | `pyproject.toml` |
| 文件 | `release-please-config.json` |
| 文件 | `requirements-dev.txt` |
| 文件 | `requirements-lock.txt` |
| 文件 | `requirements.txt` |
| 文件 | `run_checks.bat` |
| 文件 | `run_verify.bat` |
| 文件 | `start.bat` |
| 文件 | `start.sh` |

## `app/`

子目录：`integrated_app`、`models`、`perf`、`utils`、`vram`

- `__init__.py`
- `clean_launch.py`

## `app\integrated_app/`

子目录：`data`、`engines`、`locales`、`middleware`、`optimization`、`routes`、`security`、`services`、`static`、`templates`、`utils`

- `__init__.py`
- `app_server.py`
- `bad_case_retry.py`
- `cache.py`
- `checkpoint.py`
- `color_fix.py`
- `config.py`
- `config_models.py`
- `dependencies.py`
- `engine_interface.py`
- `exceptions.py`
- `gpu_backend.py`
- `gpu_utils.py`
- `history_db.py`
- `i18n.py`
- `mcp_server.py`
- `metrics.py`
- `model_manager.py`
- `model_registry.py`
- `progress.py`
- `spec.py`
- `task_queue.py`
- `video_processor.py`

## `app\integrated_app\data/`

子目录：`checkpoints`

## `app\integrated_app\engines/`

- `__init__.py`
- `_dit_pipeline.py`
- `_image_pipeline.py`
- `_memory_utils.py`
- `_vae_pipeline.py`
- `_video_pipeline.py`
- `seedvr2_engine.py`

## `app\integrated_app\locales/`

- `en.json`
- `fr.json`
- `ja.json`
- `zh-TW.json`
- `zh.json`

## `app\integrated_app\middleware/`

- `__init__.py`
- `basic_auth.py`
- `csrf.py`
- `error_handler.py`
- `rate_limit.py`
- `request_id.py`

## `app\integrated_app\optimization/`

子目录：`engine`、`gpu`、`inference`、`video`

- `__init__.py`
- `license_compliance.py`
- `roadmap.py`
- `webui_enhancement.py`

## `app\integrated_app\routes/`

子目录：`restore`、`system`、`ui`

- `__init__.py`

## `app\integrated_app\security/`

- `__init__.py`
- `integrity_check.py`
- `integrity_manifest.json`
- `integrity_selfcheck.py`
- `magic_check.py`
- `path_guard.py`
- `secret_key.py`
- `watermark.py`
- `weight_encryption.py`

## `app\integrated_app\services/`

- `__init__.py`
- `task_events.py`
- `task_state.py`

## `app\integrated_app\static/`

子目录：`css`、`fonts`、`js`、`vendor`

- `design-system.md`

## `app\integrated_app\templates/`

- `base.html`
- `history.html`
- `history_table.html`
- `index.html`
- `restore.html`
- `settings.html`
- `system_status.html`

## `app\integrated_app\utils/`

- `__init__.py`
- `fts.py`
- `response.py`
- `retry.py`

## `app\models/`

- `__init__.py`
- `lcm_distill.py`
- `raft_flow.py`
- `rife_interpolator.py`

## `app\perf/`

- `optimizer.py`

## `app\utils/`

- `experiment_tracker.py`

## `app\vram/`

- `__init__.py`
- `flash_attention_wrapper.py`

## `assets/`

子目录：`covers`

## `assets\covers/`

- `_add_text_overlay.py`

## `build/`

子目录：`SeedVR2`

## `build\SeedVR2/`

子目录：`localpycs`

- `Analysis-00.toc`
- `EXE-00.toc`
- `PKG-00.toc`
- `PYZ-00.pyz`
- `PYZ-00.toc`
- `SeedVR2.pkg`
- `base_library.zip`
- `warn-SeedVR2.txt`
- `xref-SeedVR2.html`

## `build\SeedVR2\localpycs/`

## `common/`

子目录：`diffusion`、`distributed`

- `__init__.py`
- `cache.py`
- `config.py`
- `decorators.py`
- `logger.py`
- `partition.py`
- `seed.py`
- `utils.py`

## `common\diffusion/`

子目录：`samplers`、`schedules`、`timesteps`

- `__init__.py`
- `config.py`
- `types.py`
- `utils.py`

## `common\diffusion\samplers/`

- `__init__.py`
- `base.py`
- `euler.py`

## `common\diffusion\schedules/`

- `__init__.py`
- `base.py`
- `lerp.py`

## `common\diffusion\timesteps/`

子目录：`sampling`

- `__init__.py`
- `base.py`

## `common\distributed/`

- `__init__.py`
- `advanced.py`
- `basic.py`
- `meta_init_utils.py`
- `ops.py`

## `configs_3b/`

- `config.json`

## `configs_7b/`

- `config.json`

## `data/`

子目录：`checkpoints`、`image`、`uploads`、`video`

- `history.db-shm`
- `history.db-wal`

## `data\checkpoints/`

## `data\image/`

子目录：`transforms`

## `data\image\transforms/`

- `area_resize.py`
- `divisible_crop.py`
- `na_resize.py`
- `side_resize.py`

## `data\uploads/`

子目录：`image`、`restored`、`video`

## `data\uploads\image/`

子目录：`restored`

## `data\uploads\restored/`

## `data\uploads\video/`

## `data\video/`

子目录：`transforms`

## `data\video\transforms/`

- `rearrange.py`

## `demo/`

子目录：`assets`

- `README.md`
- `index.html`

## `demo\assets/`

子目录：`inputs`、`results`

## `demo\assets\inputs/`

## `demo\assets\results/`

## `dist/`

## `docs/`

子目录：`adr`、`plans`、`project`、`prototypes`、`repo-analysis`、`reports`、`superpowers`、`ui-ux-audit-2026-07-30`、`workflows`

- `COMPLIANCE_CHECKLIST.md`
- `INSTALLER-LESSONS.md`
- `README.md`
- `SECURITY_AUDIT_REPORT.md`
- `SECURITY_AUDIT_REPORT_v2_EXECUTION_CHECKLIST.md`
- `整理记录_20260823.md`

## `docs\adr/`

- `0001-json-locales-no-gettext.md`
- `0002-test-layering-actual.md`
- `README.md`

## `docs\plans/`

- `DEPLOYMENT.md`
- `OPTIMIZATION_GUIDE.md`
- `winpython-migration-plan.md`
- `全功能实施指南.md`

## `docs\project/`

- `ARCHITECTURE.md`
- `CONSTRAINTS.md`
- `FIRST_TIME_USER_GUIDE.md`
- `PROJECT_CONTEXT.md`
- `model_algorithm_analysis.md`

## `docs\prototypes/`

- `prototype-A-sidebar-dashboard.html`
- `prototype-B-minimal-wide.html`
- `prototype-C-workspace.html`
- `prototype-D-studio.html`
- `prototype-E-workflow-glass.html`
- `prototype-F-comparison.html`
- `prototype-G-command-center.html`
- `prototype-redesign.html`
- `seedvr2-warmprint.html`
- `theme-selector.html`
- `v1 seedvr2-hybrid.html`
- `yuanbao_html_20260718_mZeher.html`

## `docs\repo-analysis/`

- `Anime4KCPP_技术学习报告.md`
- `BasicSR_技术学习报告.md`
- `BasicVSR_PlusPlus_技术学习报告.md`
- `CodeFormer_技术学习报告.md`
- `CogVideo_技术学习报告.md`
- `ComfyUI-SeedVR2_VideoUpscaler_技术学习报告.md`
- `DAIN_技术学习报告.md`
- `DeOldify_技术学习报告.md`
- `DiffBIR_技术学习报告.md`
- `DiffVSR_技术学习报告.md`
- `EvTexture_技术学习报告.md`
- `FTVSR_技术学习报告.md`
- `Fast-SRGAN_技术学习报告.md`
- `FlashVSR-v2_技术学习报告.md`
- `FlashVSR_技术学习报告.md`
- `HunyuanVideo_技术学习报告.md`
- `MIA-VSR_技术学习报告.md`
- `Open-Sora-Plan_技术学习报告.md`
- `PaddleGAN_技术学习报告.md`
- `ProPainter_技术学习报告.md`
- `RVRT_技术学习报告.md`
- `Real-ESRGAN_技术学习报告.md`
- `SCST_技术学习报告.md`
- `STAR_技术学习报告.md`
- `SUPIR_技术学习报告.md`
- `SeedVR2-3B_技术学习报告.md`
- `StableVSR_技术学习报告.md`
- `Stream-DiffVSR_技术学习报告.md`
- `Turtle_技术学习报告.md`
- `Upscale-A-Video_技术学习报告.md`
- `VEnhancer_技术学习报告.md`
- `Vivid-VR_技术学习报告.md`
- `Waifu2x-Extension-GUI_技术学习报告.md`
- `bilibili-ailab_技术学习报告.md`
- `clarity-upscaler_技术学习报告.md`
- `similar-repos-analysis.md`
- `upscayl_技术学习报告.md`
- `waifu2x_技术学习报告.md`
- `综合分析报告-reformatted.md`
- `综合分析报告.md`

## `docs\reports/`

- `CI-LESSONS.md`
- `INSTALLER-LESSONS.md`
- `LOGGING_AUDIT_REPORT.md`
- `SageAttention调研备忘.md`
- `TEST_SYSTEM_AUDIT_REPORT.md`
- `UX-UI深度评估报告.md`
- `UX-UI评估报告.md`
- `config_review.md`
- `design-style-analysis-2026-07-18.md`
- `seedvr2-design-analysis.md`
- `seedvr2-ui-audit-report.md`
- `功能实现状态分析报告.md`
- `测试体系完整性评估报告.md`
- `项目健康度评估报告.md`

## `docs\superpowers/`

子目录：`plans`、`specs`

## `docs\superpowers\plans/`

- `2026-08-21-desktop-installer.md`

## `docs\superpowers\specs/`

- `2026-08-21-desktop-installer-design.md`

## `docs\ui-ux-audit-2026-07-30/`

子目录：`after-screenshots`、`baseline-screenshots`

## `docs\ui-ux-audit-2026-07-30\after-screenshots/`

## `docs\ui-ux-audit-2026-07-30\baseline-screenshots/`

## `docs\workflows/`

- `SeedVR2.json`

## `dogfood-output/`

子目录：`videos`

- `report.md`

## `dogfood-output\videos/`

## `examples/`

- `README.md`
- `api_example.js`
- `api_example.py`

## `launcher/`

子目录：`static`、`torch_wheels`

- `bootstrap_server.py`
- `dependency_check.py`
- `env_check.py`
- `installer.iss`
- `installer_full.iss`
- `installer_full_info.txt`
- `installer_torch.iss`
- `installer_torch_info.txt`
- `launcher_main.py`
- `model_check.py`
- `python_env.py`
- `release-notes-intro.md`
- `requirements-small.txt`
- `setup_state.py`
- `smoke_test.py`

## `launcher\static/`

- `app.js`
- `index.html`
- `style.css`

## `launcher\torch_wheels/`

- `torch-2.11.0+cu128-cp312-cp312-win_amd64.whl`
- `torchaudio-2.11.0+cu128-cp312-cp312-win_amd64.whl`
- `torchvision-0.26.0+cu128-cp312-cp312-win_amd64.whl`

## `logs/`

子目录：`pytest-basetemp`、`pytest-tmp`、`torchinductor_Doro`

- `gpu_monitor.csv`
- `gpu_monitor_conv3d.csv`
- `gpu_monitor_preset.csv`
- `gpu_monitor_retest.csv`
- `gpu_monitor_round3.csv`
- `gpu_monitor_video_preset.csv`
- `monitor_gpu.ps1`

## `logs\pytest-basetemp/`

子目录：`test_absolute_path_outside_roo0`、`test_add_record_returns_id0`、`test_add_records_batch0`、`test_add_records_empty_list0`、`test_aenter_returns_self0`、`test_aexit_closes_even_on_exce0`、`test_all_api_paths_have_respon0`、`test_assert_safe_custom_messag0`、`test_assert_safe_download0`、`test_assert_safe_passes_for_sa0`、`test_assert_safe_raises_403_fo0`、`test_assert_safe_scan0`、`test_batch_progress_nonexisten0`、`test_below_min_value_returns_e0`、`test_build_default_path_guard_0`、`test_busy_timeout_pragma_appli0`、`test_cancel_nonexistent_return0`、`test_cancel_nonexistent_task_r0`、`test_cfg_scale_validation0`、`test_cleanup_expired0`、`test_cleanup_no_expired0`、`test_clear_all0`、`test_close_idempotent0`、`test_close_resets_state_even_o0`、`test_create_and_get_task0`、`test_default_dirs_present0`、`test_default_preferences_have_0`、`test_delete_file0`、`test_delete_nonexistent_file0`、`test_delete_record0`、`test_delete_task0`、`test_denoising_strength_valida0`、`test_download_nonexistent_retu0`、`test_download_nonexistent_task0`、`test_empty_extra_dirs0`、`test_empty_table_shows_no_reco0`、`test_error_response_has_detail0`、`test_error_responses_defined0`、`test_extra_absolute_dir_kept_a0`、`test_extra_dirs_in_default_bui0`、`test_extra_dirs_none_in_defaul0`、`test_extra_relative_dir_resolv0`、`test_file_cache_cleanup_with_s0`、`test_file_cache_clear_all_with0`、`test_file_cache_stats_with_sub0`、`test_file_exists0`、`test_generate_unique_filename_0`、`test_generate_unique_filename_1`、`test_generate_unique_filename_2`、`test_get_cache_path0`、`test_get_cache_stats_empty0`、`test_get_cache_stats_with_file0`、`test_get_file_path0`、`test_get_file_path_nonexistent0`、`test_get_gpu_info0`、`test_get_gpu_system_info0`、`test_get_incomplete_tasks0`、`test_get_inference_history_ret0`、`test_get_locales_returns_list0`、`test_get_metrics_returns_snaps0`、`test_get_nonexistent_record_re0`、`test_get_record_by_id0`、`test_get_records_by_ids_batch0`、`test_get_records_by_ids_dedup0`、`test_get_records_by_ids_empty_0`、`test_get_records_by_ids_with_n0`、`test_get_settings_returns_conf0`、`test_get_video_info_invalid_js0`、`test_get_video_info_not_found0`、`test_get_video_info_success0`、`test_groups_contain_required_f0`、`test_groups_sorted_by_priority0`、`test_has_three_default_groups0`、`test_health_endpoint_has_200_r0`、`test_health_response_structure0`、`test_health_returns_detailed_i0`、`test_history_pagination_large_0`、`test_history_pagination_max_pa0`、`test_history_pagination_oversi0`、`test_history_pagination_page_z0`、`test_history_response_structur0`、`test_history_returns_json0`、`test_history_table_htmx_return0`、`test_index_returns_200_and_con0`、`test_initialize_creates_parent0`、`test_initialize_creates_tables0`、`test_initialize_idempotent0`、`test_insufficient_ram_rejects_0`、`test_invalid_page_returns_4220`、`test_invalid_path_returns_fals0`、`test_invalid_path_skipped_sile0`、`test_model_load_with_mock0`、`test_model_status_returns_stat0`、`test_model_unload_with_mock0`、`test_models_destroyed_on_succe0`、`test_multiple_allowed_dirs0`、`test_multiple_segments_long_vi0`、`test_openapi_schema_generated0`、`test_out_of_range_value_return0`、`test_oversize_page_size_return0`、`test_parameters_contain_requir0`、`test_parameters_include_core_f0`、`test_partial_update_preserves_0`、`test_path_equals_allowed_dir0`、`test_path_inside_allowed_dir0`、`test_path_outside_allowed_dir0`、`test_path_traversal_blocked0`、`test_path_traversal_with_dotdo0`、`test_ping_returns_ok0`、`test_presets_contain_required_0`、`test_presets_contain_three_def0`、`test_progress_callback_reporte0`、`test_progress_nonexistent_retu0`、`test_progress_nonexistent_task0`、`test_recommendations_contain_r0`、`test_recommendations_sorted_by0`、`test_recommendations_with_cust0`、`test_relative_path_resolved_to0`、`test_reset_restores_defaults0`、`test_resolves_allowed_dirs0`、`test_restore_endpoints_require0`、`test_restore_page_returns_2000`、`test_restore_progress_nonexist0`、`test_restore_without_input_ret0`、`test_restore_without_model_ret0`、`test_result_is_valid_fts5_synt0`、`test_result_nonexistent_return0`、`test_result_nonexistent_task_r0`、`test_returns_success_and_data0`、`test_returns_success_and_data1`、`test_returns_success_and_group0`、`test_returns_success_and_recom0`、`test_safe_path_inside_allowed0`、`test_save_and_reload0`、`test_save_bytes0`、`test_save_bytes_with_subdir0`、`test_scan_folder_not_found_in_0`、`test_scan_folder_outside_white0`、`test_schema_has_security_schem0`、`test_search_basic_match0`、`test_search_empty_query_return0`、`test_search_pagination0`、`test_search_with_fts5_injectio0`、`test_set_locale_to_en0`、`test_single_segment_short_vide0`、`test_stop_cleanup_task_when_no0`、`test_success_response_has_succ0`、`test_table_contains_record0`、`test_table_htmx_header_not_req0`、`test_table_search_filters_reco0`、`test_unknown_param_ignored0`、`test_unsafe_path_outside_allow0`、`test_update_record_rejects_unk0`、`test_update_record_whitelist0`、`test_update_settings_round_tri0`、`test_update_task_rejects_unkno0`、`test_update_task_whitelist0`、`test_valid_page_returns_2000`、`test_valid_page_size_returns_20`、`test_valid_values_return_no_er0`、`test_video_info_invalid_return0`

- `test_absolute_path_outside_roocurrent`
- `test_add_record_returns_idcurrent`
- `test_add_records_batchcurrent`
- `test_add_records_empty_listcurrent`
- `test_aenter_returns_selfcurrent`
- `test_aexit_closes_even_on_excecurrent`
- `test_all_api_paths_have_responcurrent`
- `test_assert_safe_custom_messagcurrent`
- `test_assert_safe_downloadcurrent`
- `test_assert_safe_passes_for_sacurrent`
- `test_assert_safe_raises_403_focurrent`
- `test_assert_safe_scancurrent`
- `test_batch_progress_nonexistencurrent`
- `test_below_min_value_returns_ecurrent`
- `test_build_default_path_guard_current`
- `test_busy_timeout_pragma_applicurrent`
- `test_cancel_nonexistent_returncurrent`
- `test_cancel_nonexistent_task_rcurrent`
- `test_cfg_scale_validationcurrent`
- `test_cleanup_expiredcurrent`
- `test_cleanup_no_expiredcurrent`
- `test_clear_allcurrent`
- `test_close_idempotentcurrent`
- `test_close_resets_state_even_ocurrent`
- `test_create_and_get_taskcurrent`
- `test_default_dirs_presentcurrent`
- `test_default_preferences_have_current`
- `test_delete_filecurrent`
- `test_delete_nonexistent_filecurrent`
- `test_delete_recordcurrent`
- `test_delete_taskcurrent`
- `test_denoising_strength_validacurrent`
- `test_download_nonexistent_retucurrent`
- `test_download_nonexistent_taskcurrent`
- `test_empty_extra_dirscurrent`
- `test_empty_table_shows_no_recocurrent`
- `test_error_response_has_detailcurrent`
- `test_error_responses_definedcurrent`
- `test_extra_absolute_dir_kept_acurrent`
- `test_extra_dirs_in_default_buicurrent`
- `test_extra_dirs_none_in_defaulcurrent`
- `test_extra_relative_dir_resolvcurrent`
- `test_file_cache_cleanup_with_scurrent`
- `test_file_cache_clear_all_withcurrent`
- `test_file_cache_stats_with_subcurrent`
- `test_file_existscurrent`
- `test_generate_unique_filename_current`
- `test_get_cache_pathcurrent`
- `test_get_cache_stats_emptycurrent`
- `test_get_cache_stats_with_filecurrent`
- `test_get_file_path_nonexistentcurrent`
- `test_get_file_pathcurrent`
- `test_get_gpu_infocurrent`
- `test_get_gpu_system_infocurrent`
- `test_get_incomplete_taskscurrent`
- `test_get_inference_history_retcurrent`
- `test_get_locales_returns_listcurrent`
- `test_get_metrics_returns_snapscurrent`
- `test_get_nonexistent_record_recurrent`
- `test_get_record_by_idcurrent`
- `test_get_records_by_ids_batchcurrent`
- `test_get_records_by_ids_dedupcurrent`
- `test_get_records_by_ids_empty_current`
- `test_get_records_by_ids_with_ncurrent`
- `test_get_settings_returns_confcurrent`
- `test_get_video_info_invalid_jscurrent`
- `test_get_video_info_not_foundcurrent`
- `test_get_video_info_successcurrent`
- `test_groups_contain_required_fcurrent`
- `test_groups_sorted_by_prioritycurrent`
- `test_has_three_default_groupscurrent`
- `test_health_endpoint_has_200_rcurrent`
- `test_health_response_structurecurrent`
- `test_health_returns_detailed_icurrent`
- `test_history_pagination_large_current`
- `test_history_pagination_max_pacurrent`
- `test_history_pagination_oversicurrent`
- `test_history_pagination_page_zcurrent`
- `test_history_response_structurcurrent`
- `test_history_returns_jsoncurrent`
- `test_history_table_htmx_returncurrent`
- `test_index_returns_200_and_concurrent`
- `test_initialize_creates_parentcurrent`
- `test_initialize_creates_tablescurrent`
- `test_initialize_idempotentcurrent`
- `test_insufficient_ram_rejects_current`
- `test_invalid_page_returns_422current`
- `test_invalid_path_returns_falscurrent`
- `test_invalid_path_skipped_silecurrent`
- `test_model_load_with_mockcurrent`
- `test_model_status_returns_statcurrent`
- `test_model_unload_with_mockcurrent`
- `test_models_destroyed_on_succecurrent`
- `test_multiple_allowed_dirscurrent`
- `test_multiple_segments_long_vicurrent`
- `test_openapi_schema_generatedcurrent`
- `test_out_of_range_value_returncurrent`
- `test_oversize_page_size_returncurrent`
- `test_parameters_contain_requircurrent`
- `test_parameters_include_core_fcurrent`
- `test_partial_update_preserves_current`
- `test_path_equals_allowed_dircurrent`
- `test_path_inside_allowed_dircurrent`
- `test_path_outside_allowed_dircurrent`
- `test_path_traversal_blockedcurrent`
- `test_path_traversal_with_dotdocurrent`
- `test_ping_returns_okcurrent`
- `test_presets_contain_required_current`
- `test_presets_contain_three_defcurrent`
- `test_progress_callback_reportecurrent`
- `test_progress_nonexistent_retucurrent`
- `test_progress_nonexistent_taskcurrent`
- `test_recommendations_contain_rcurrent`
- `test_recommendations_sorted_bycurrent`
- `test_recommendations_with_custcurrent`
- `test_relative_path_resolved_tocurrent`
- `test_reset_restores_defaultscurrent`
- `test_resolves_allowed_dirscurrent`
- `test_restore_endpoints_requirecurrent`
- `test_restore_page_returns_200current`
- `test_restore_progress_nonexistcurrent`
- `test_restore_without_input_retcurrent`
- `test_restore_without_model_retcurrent`
- `test_result_is_valid_fts5_syntcurrent`
- `test_result_nonexistent_returncurrent`
- `test_result_nonexistent_task_rcurrent`
- `test_returns_success_and_datacurrent`
- `test_returns_success_and_groupcurrent`
- `test_returns_success_and_recomcurrent`
- `test_safe_path_inside_allowedcurrent`
- `test_save_and_reloadcurrent`
- `test_save_bytes_with_subdircurrent`
- `test_save_bytescurrent`
- `test_scan_folder_not_found_in_current`
- `test_scan_folder_outside_whitecurrent`
- `test_schema_has_security_schemcurrent`
- `test_search_basic_matchcurrent`
- `test_search_empty_query_returncurrent`
- `test_search_paginationcurrent`
- `test_search_with_fts5_injectiocurrent`
- `test_set_locale_to_encurrent`
- `test_single_segment_short_videcurrent`
- `test_stop_cleanup_task_when_nocurrent`
- `test_success_response_has_succcurrent`
- `test_table_contains_recordcurrent`
- `test_table_htmx_header_not_reqcurrent`
- `test_table_search_filters_recocurrent`
- `test_unknown_param_ignoredcurrent`
- `test_unsafe_path_outside_allowcurrent`
- `test_update_record_rejects_unkcurrent`
- `test_update_record_whitelistcurrent`
- `test_update_settings_round_tricurrent`
- `test_update_task_rejects_unknocurrent`
- `test_update_task_whitelistcurrent`
- `test_valid_page_returns_200current`
- `test_valid_page_size_returns_2current`
- `test_valid_values_return_no_ercurrent`
- `test_video_info_invalid_returncurrent`

## `logs\pytest-basetemp\test_absolute_path_outside_roo0/`

子目录：`outputs`

## `logs\pytest-basetemp\test_add_record_returns_id0/`

## `logs\pytest-basetemp\test_add_records_batch0/`

## `logs\pytest-basetemp\test_add_records_empty_list0/`

## `logs\pytest-basetemp\test_aenter_returns_self0/`

## `logs\pytest-basetemp\test_aexit_closes_even_on_exce0/`

## `logs\pytest-basetemp\test_all_api_paths_have_respon0/`

## `logs\pytest-basetemp\test_assert_safe_custom_messag0/`

## `logs\pytest-basetemp\test_assert_safe_download0/`

子目录：`outputs`

## `logs\pytest-basetemp\test_assert_safe_passes_for_sa0/`

子目录：`outputs`

## `logs\pytest-basetemp\test_assert_safe_raises_403_fo0/`

子目录：`outputs`

## `logs\pytest-basetemp\test_assert_safe_scan0/`

子目录：`outputs`

## `logs\pytest-basetemp\test_batch_progress_nonexisten0/`

## `logs\pytest-basetemp\test_below_min_value_returns_e0/`

## `logs\pytest-basetemp\test_build_default_path_guard_0/`

## `logs\pytest-basetemp\test_busy_timeout_pragma_appli0/`

## `logs\pytest-basetemp\test_cancel_nonexistent_return0/`

## `logs\pytest-basetemp\test_cancel_nonexistent_task_r0/`

## `logs\pytest-basetemp\test_cfg_scale_validation0/`

## `logs\pytest-basetemp\test_cleanup_expired0/`

## `logs\pytest-basetemp\test_cleanup_no_expired0/`

- `1786359934_956706714611.txt`

## `logs\pytest-basetemp\test_clear_all0/`

## `logs\pytest-basetemp\test_close_idempotent0/`

## `logs\pytest-basetemp\test_close_resets_state_even_o0/`

## `logs\pytest-basetemp\test_create_and_get_task0/`

## `logs\pytest-basetemp\test_default_dirs_present0/`

## `logs\pytest-basetemp\test_default_preferences_have_0/`

## `logs\pytest-basetemp\test_delete_file0/`

## `logs\pytest-basetemp\test_delete_nonexistent_file0/`

## `logs\pytest-basetemp\test_delete_record0/`

## `logs\pytest-basetemp\test_delete_task0/`

## `logs\pytest-basetemp\test_denoising_strength_valida0/`

## `logs\pytest-basetemp\test_download_nonexistent_retu0/`

## `logs\pytest-basetemp\test_download_nonexistent_task0/`

## `logs\pytest-basetemp\test_empty_extra_dirs0/`

## `logs\pytest-basetemp\test_empty_table_shows_no_reco0/`

## `logs\pytest-basetemp\test_error_response_has_detail0/`

## `logs\pytest-basetemp\test_error_responses_defined0/`

## `logs\pytest-basetemp\test_extra_absolute_dir_kept_a0/`

子目录：`external`

## `logs\pytest-basetemp\test_extra_dirs_in_default_bui0/`

## `logs\pytest-basetemp\test_extra_dirs_none_in_defaul0/`

## `logs\pytest-basetemp\test_extra_relative_dir_resolv0/`

## `logs\pytest-basetemp\test_file_cache_cleanup_with_s0/`

子目录：`sub`

## `logs\pytest-basetemp\test_file_cache_clear_all_with0/`

子目录：`sub`

## `logs\pytest-basetemp\test_file_cache_stats_with_sub0/`

子目录：`sub1`、`sub2`

## `logs\pytest-basetemp\test_file_exists0/`

- `1786359933_563191a719c1.txt`

## `logs\pytest-basetemp\test_generate_unique_filename_0/`

## `logs\pytest-basetemp\test_generate_unique_filename_1/`

## `logs\pytest-basetemp\test_generate_unique_filename_2/`

## `logs\pytest-basetemp\test_get_cache_path0/`

## `logs\pytest-basetemp\test_get_cache_stats_empty0/`

## `logs\pytest-basetemp\test_get_cache_stats_with_file0/`

- `1786359934_e61b1fe44199.txt`

## `logs\pytest-basetemp\test_get_file_path0/`

- `1786359933_d172cdde73ed.txt`

## `logs\pytest-basetemp\test_get_file_path_nonexistent0/`

## `logs\pytest-basetemp\test_get_gpu_info0/`

## `logs\pytest-basetemp\test_get_gpu_system_info0/`

## `logs\pytest-basetemp\test_get_incomplete_tasks0/`

## `logs\pytest-basetemp\test_get_inference_history_ret0/`

## `logs\pytest-basetemp\test_get_locales_returns_list0/`

## `logs\pytest-basetemp\test_get_metrics_returns_snaps0/`

## `logs\pytest-basetemp\test_get_nonexistent_record_re0/`

## `logs\pytest-basetemp\test_get_record_by_id0/`

## `logs\pytest-basetemp\test_get_records_by_ids_batch0/`

## `logs\pytest-basetemp\test_get_records_by_ids_dedup0/`

## `logs\pytest-basetemp\test_get_records_by_ids_empty_0/`

## `logs\pytest-basetemp\test_get_records_by_ids_with_n0/`

## `logs\pytest-basetemp\test_get_settings_returns_conf0/`

## `logs\pytest-basetemp\test_get_video_info_invalid_js0/`

## `logs\pytest-basetemp\test_get_video_info_not_found0/`

## `logs\pytest-basetemp\test_get_video_info_success0/`

## `logs\pytest-basetemp\test_groups_contain_required_f0/`

## `logs\pytest-basetemp\test_groups_sorted_by_priority0/`

## `logs\pytest-basetemp\test_has_three_default_groups0/`

## `logs\pytest-basetemp\test_health_endpoint_has_200_r0/`

## `logs\pytest-basetemp\test_health_response_structure0/`

## `logs\pytest-basetemp\test_health_returns_detailed_i0/`

## `logs\pytest-basetemp\test_history_pagination_large_0/`

## `logs\pytest-basetemp\test_history_pagination_max_pa0/`

## `logs\pytest-basetemp\test_history_pagination_oversi0/`

## `logs\pytest-basetemp\test_history_pagination_page_z0/`

## `logs\pytest-basetemp\test_history_response_structur0/`

## `logs\pytest-basetemp\test_history_returns_json0/`

## `logs\pytest-basetemp\test_history_table_htmx_return0/`

## `logs\pytest-basetemp\test_index_returns_200_and_con0/`

## `logs\pytest-basetemp\test_initialize_creates_parent0/`

子目录：`nested`

## `logs\pytest-basetemp\test_initialize_creates_tables0/`

## `logs\pytest-basetemp\test_initialize_idempotent0/`

## `logs\pytest-basetemp\test_insufficient_ram_rejects_0/`

## `logs\pytest-basetemp\test_invalid_page_returns_4220/`

## `logs\pytest-basetemp\test_invalid_path_returns_fals0/`

## `logs\pytest-basetemp\test_invalid_path_skipped_sile0/`

## `logs\pytest-basetemp\test_model_load_with_mock0/`

## `logs\pytest-basetemp\test_model_status_returns_stat0/`

## `logs\pytest-basetemp\test_model_unload_with_mock0/`

## `logs\pytest-basetemp\test_models_destroyed_on_succe0/`

子目录：`out`

## `logs\pytest-basetemp\test_multiple_allowed_dirs0/`

子目录：`data`、`outputs`

## `logs\pytest-basetemp\test_multiple_segments_long_vi0/`

子目录：`out`

## `logs\pytest-basetemp\test_openapi_schema_generated0/`

## `logs\pytest-basetemp\test_out_of_range_value_return0/`

## `logs\pytest-basetemp\test_oversize_page_size_return0/`

## `logs\pytest-basetemp\test_parameters_contain_requir0/`

## `logs\pytest-basetemp\test_parameters_include_core_f0/`

## `logs\pytest-basetemp\test_partial_update_preserves_0/`

## `logs\pytest-basetemp\test_path_equals_allowed_dir0/`

子目录：`outputs`

## `logs\pytest-basetemp\test_path_inside_allowed_dir0/`

子目录：`outputs`

## `logs\pytest-basetemp\test_path_outside_allowed_dir0/`

子目录：`outputs`

## `logs\pytest-basetemp\test_path_traversal_blocked0/`

## `logs\pytest-basetemp\test_path_traversal_with_dotdo0/`

子目录：`outputs`

## `logs\pytest-basetemp\test_ping_returns_ok0/`

## `logs\pytest-basetemp\test_presets_contain_required_0/`

## `logs\pytest-basetemp\test_presets_contain_three_def0/`

## `logs\pytest-basetemp\test_progress_callback_reporte0/`

子目录：`out`

## `logs\pytest-basetemp\test_progress_nonexistent_retu0/`

## `logs\pytest-basetemp\test_progress_nonexistent_task0/`

## `logs\pytest-basetemp\test_recommendations_contain_r0/`

## `logs\pytest-basetemp\test_recommendations_sorted_by0/`

## `logs\pytest-basetemp\test_recommendations_with_cust0/`

## `logs\pytest-basetemp\test_relative_path_resolved_to0/`

## `logs\pytest-basetemp\test_reset_restores_defaults0/`

## `logs\pytest-basetemp\test_resolves_allowed_dirs0/`

子目录：`outputs`

## `logs\pytest-basetemp\test_restore_endpoints_require0/`

## `logs\pytest-basetemp\test_restore_page_returns_2000/`

## `logs\pytest-basetemp\test_restore_progress_nonexist0/`

## `logs\pytest-basetemp\test_restore_without_input_ret0/`

## `logs\pytest-basetemp\test_restore_without_model_ret0/`

## `logs\pytest-basetemp\test_result_is_valid_fts5_synt0/`

## `logs\pytest-basetemp\test_result_nonexistent_return0/`

## `logs\pytest-basetemp\test_result_nonexistent_task_r0/`

## `logs\pytest-basetemp\test_returns_success_and_data0/`

## `logs\pytest-basetemp\test_returns_success_and_data1/`

## `logs\pytest-basetemp\test_returns_success_and_group0/`

## `logs\pytest-basetemp\test_returns_success_and_recom0/`

## `logs\pytest-basetemp\test_safe_path_inside_allowed0/`

子目录：`subdir`

## `logs\pytest-basetemp\test_save_and_reload0/`

## `logs\pytest-basetemp\test_save_bytes0/`

## `logs\pytest-basetemp\test_save_bytes_with_subdir0/`

子目录：`sub`

## `logs\pytest-basetemp\test_scan_folder_not_found_in_0/`

## `logs\pytest-basetemp\test_scan_folder_outside_white0/`

## `logs\pytest-basetemp\test_schema_has_security_schem0/`

## `logs\pytest-basetemp\test_search_basic_match0/`

## `logs\pytest-basetemp\test_search_empty_query_return0/`

## `logs\pytest-basetemp\test_search_pagination0/`

## `logs\pytest-basetemp\test_search_with_fts5_injectio0/`

## `logs\pytest-basetemp\test_set_locale_to_en0/`

- `config.yaml`

## `logs\pytest-basetemp\test_single_segment_short_vide0/`

子目录：`out`

## `logs\pytest-basetemp\test_stop_cleanup_task_when_no0/`

## `logs\pytest-basetemp\test_success_response_has_succ0/`

## `logs\pytest-basetemp\test_table_contains_record0/`

## `logs\pytest-basetemp\test_table_htmx_header_not_req0/`

## `logs\pytest-basetemp\test_table_search_filters_reco0/`

## `logs\pytest-basetemp\test_unknown_param_ignored0/`

## `logs\pytest-basetemp\test_unsafe_path_outside_allow0/`

## `logs\pytest-basetemp\test_update_record_rejects_unk0/`

## `logs\pytest-basetemp\test_update_record_whitelist0/`

## `logs\pytest-basetemp\test_update_settings_round_tri0/`

- `config.yaml`

## `logs\pytest-basetemp\test_update_task_rejects_unkno0/`

## `logs\pytest-basetemp\test_update_task_whitelist0/`

## `logs\pytest-basetemp\test_valid_page_returns_2000/`

## `logs\pytest-basetemp\test_valid_page_size_returns_20/`

## `logs\pytest-basetemp\test_valid_values_return_no_er0/`

## `logs\pytest-basetemp\test_video_info_invalid_return0/`

## `logs\pytest-tmp/`

## `logs\torchinductor_Doro/`

## `model/`

## `model_lib/`

子目录：`common`、`dit`、`dit_v2`、`video_vae_v3`

- `SOURCE.md`
- `__init__.py`

## `model_lib\common/`

- `__init__.py`
- `context_parallel.py`
- `fp8.py`
- `moe.py`

## `model_lib\dit/`

子目录：`blocks`、`nablocks`

- `__init__.py`
- `attention.py`
- `embedding.py`
- `mlp.py`
- `mm.py`
- `modulation.py`
- `na.py`
- `nadit.py`
- `normalization.py`
- `patch.py`
- `rope.py`
- `window.py`

## `model_lib\dit\blocks/`

- `__init__.py`
- `mmdit_window_block.py`

## `model_lib\dit\nablocks/`

- `__init__.py`
- `mmsr_block.py`

## `model_lib\dit_v2/`

子目录：`nablocks`、`patch`

- `__init__.py`
- `attention.py`
- `embedding.py`
- `mlp.py`
- `mm.py`
- `modulation.py`
- `na.py`
- `nadit.py`
- `normalization.py`
- `rope.py`
- `window.py`

## `model_lib\dit_v2\nablocks/`

子目录：`attention`

- `__init__.py`
- `mmsr_block.py`

## `model_lib\dit_v2\patch/`

- `__init__.py`
- `patch_v1.py`

## `model_lib\video_vae_v3/`

子目录：`modules`

- `__init__.py`
- `s8_c16_t4_inflation_sd3.yaml`

## `model_lib\video_vae_v3\modules/`

- `__init__.py`
- `attn_video_vae.py`
- `causal_inflation_lib.py`
- `context_parallel_lib.py`
- `global_config.py`
- `inflated_layers.py`
- `inflated_lib.py`
- `types.py`
- `video_vae.py`

## `outputs/`

子目录：`image`、`video`

## `outputs\image/`

子目录：`restored`

## `outputs\image\restored/`

## `outputs\video/`

## `perf/`

子目录：`benchmark`、`results`

- `__init__.py`
- `monitoring_plan.md`

## `perf\benchmark/`

- `BENCHMARK_GUIDE.md`
- `__init__.py`
- `bench_restore_api.py`
- `flash_attention_benchmark.py`
- `flash_attn_benchmark.py`
- `install_flash_attn.bat`
- `run_benchmark.sh`
- `test_suite.py`

## `perf\results/`

## `scripts/`

子目录：`git-hooks`

- `backup-db.bat`
- `backup-db.sh`
- `build_dual_installers.ps1`
- `capture-screenshots.bat`
- `check_i18n_keys.py`
- `check_local.py`
- `check_spec_refs.py`
- `download_model.py`
- `explore_repos.ps1`
- `export_model.py`
- `generate_integrity_manifest.py`
- `generate_lock.py`
- `generate_markdown.ps1`
- `init_watermark_key.py`
- `install-hooks.ps1`
- `parse_repos.ps1`
- `perf_monitor.py`
- `render_pages.py`
- `setup_winpython.py`
- `simple_summary.ps1`
- `smoke_test_security.py`
- `verify_engine.py`

## `scripts\git-hooks/`

## `tests/`

子目录：`fixtures`、`frontend`、`pages`、`perf`、`playwright-report`、`reports`、`specs`、`test-assets`、`test-results`、`utils`、`wcag-reports`

- `USER_BEHAVIOR_TEST_REPORT.md`
- `__init__.py`
- `api_test_runner.py`
- `capture-screenshots.js`
- `conftest.py`
- `generate_test_assets.py`
- `package-lock.json`
- `package.json`
- `playwright.config.ts`
- `test_api.py`
- `test_api_schema.py`
- `test_bad_case_retry.py`
- `test_basic_auth.py`
- `test_cache.py`
- `test_color_fix.py`
- `test_config_models.py`
- `test_csrf_signed.py`
- `test_error_handler.py`
- `test_exceptions.py`
- `test_factory.py`
- `test_fts_escape.py`
- `test_gpu_backend.py`
- `test_gpu_utils.py`
- `test_history_db.py`
- `test_history_htmx.py`
- `test_i18n.py`
- `test_integrity_check.py`
- `test_launcher_bootstrap_server.py`
- `test_launcher_dependency_check.py`
- `test_launcher_env_check.py`
- `test_launcher_model_check.py`
- `test_launcher_python_env.py`
- `test_launcher_setup_state.py`
- `test_launcher_smoke_test.py`
- `test_logger.py`
- `test_magic_check.py`
- `test_mcp_server.py`
- `test_metrics.py`
- `test_model_manager.py`
- `test_model_registry.py`
- `test_path_guard.py`
- `test_property_based.py`
- `test_rate_limit.py`
- `test_recovery.py`
- `test_refactor_e4_b2.py`
- `test_request_id.py`
- `test_response.py`
- `test_retry.py`
- `test_settings_routes.py`
- `test_spec.py`
- `test_sse_integration.py`
- `test_sse_session_filter.py`
- `test_task_events.py`
- `test_task_queue.py`
- `test_task_state.py`
- `test_ui_routes.py`
- `test_video_pipeline.py`
- `test_video_processor.py`
- `test_watermark.py`
- `test_webui_filelist.py`
- `test_weight_encryption.py`
- `tsconfig.json`
- `wcag-contrast-test.js`

## `tests\fixtures/`

- `api-mocks.ts`
- `test-data.ts`

## `tests\frontend/`

- `README.md`
- `smoke.js`

## `tests\pages/`

- `base.page.ts`
- `history.page.ts`
- `image-restore.page.ts`
- `index.page.ts`
- `settings.page.ts`
- `system-status.page.ts`
- `video-restore.page.ts`

## `tests\perf/`

- `__init__.py`
- `locustfile.py`

## `tests\playwright-report/`

- `index.html`

## `tests\reports/`

- `bug-report-template.md`
- `test-summary-template.md`

## `tests\specs/`

子目录：`uiux-compatibility.spec.ts-snapshots`

- `a11y.spec.ts`
- `history.spec.ts`
- `i18n.spec.ts`
- `image-restore.spec.ts`
- `navigation.spec.ts`
- `network-conditions.spec.ts`
- `performance.spec.ts`
- `security.spec.ts`
- `settings.spec.ts`
- `sse.spec.ts`
- `system-status.spec.ts`
- `theme.spec.ts`
- `uiux-compatibility.spec.ts`
- `video-restore.spec.ts`
- `wcag-contrast.spec.ts`

## `tests\specs\uiux-compatibility.spec.ts-snapshots/`

## `tests\test-assets/`

子目录：`images`、`videos`

## `tests\test-assets\images/`

## `tests\test-assets\videos/`

## `tests\test-results/`

子目录：`uiux-compatibility-Visual--033f6-ght-theme-visual-regression-chromium-desktop`、`uiux-compatibility-Visual--1372f-ght-theme-visual-regression-chromium-desktop`、`uiux-compatibility-Visual--2553f-ark-theme-visual-regression-chromium-desktop`、`uiux-compatibility-Visual--2a767-ark-theme-visual-regression-chromium-desktop`、`uiux-compatibility-Visual--5169e-ght-theme-visual-regression-chromium-desktop`、`uiux-compatibility-Visual--51d24-ark-theme-visual-regression-chromium-desktop`、`uiux-compatibility-Visual--7a7b0-ark-theme-visual-regression-chromium-desktop`、`uiux-compatibility-Visual--854db-ght-theme-visual-regression-chromium-desktop`、`uiux-compatibility-Visual--877b7-ght-theme-visual-regression-chromium-desktop`、`uiux-compatibility-Visual--a06d5-ark-theme-visual-regression-chromium-desktop`、`uiux-compatibility-Visual--e2829-ght-theme-visual-regression-chromium-desktop`、`uiux-compatibility-Visual--fd9a9-ark-theme-visual-regression-chromium-desktop`

## `tests\test-results\uiux-compatibility-Visual--033f6-ght-theme-visual-regression-chromium-desktop/`

- `error-context.md`

## `tests\test-results\uiux-compatibility-Visual--1372f-ght-theme-visual-regression-chromium-desktop/`

- `error-context.md`

## `tests\test-results\uiux-compatibility-Visual--2553f-ark-theme-visual-regression-chromium-desktop/`

- `error-context.md`

## `tests\test-results\uiux-compatibility-Visual--2a767-ark-theme-visual-regression-chromium-desktop/`

- `error-context.md`

## `tests\test-results\uiux-compatibility-Visual--5169e-ght-theme-visual-regression-chromium-desktop/`

- `error-context.md`

## `tests\test-results\uiux-compatibility-Visual--51d24-ark-theme-visual-regression-chromium-desktop/`

- `error-context.md`

## `tests\test-results\uiux-compatibility-Visual--7a7b0-ark-theme-visual-regression-chromium-desktop/`

- `error-context.md`

## `tests\test-results\uiux-compatibility-Visual--854db-ght-theme-visual-regression-chromium-desktop/`

- `error-context.md`

## `tests\test-results\uiux-compatibility-Visual--877b7-ght-theme-visual-regression-chromium-desktop/`

- `error-context.md`

## `tests\test-results\uiux-compatibility-Visual--a06d5-ark-theme-visual-regression-chromium-desktop/`

- `error-context.md`

## `tests\test-results\uiux-compatibility-Visual--e2829-ght-theme-visual-regression-chromium-desktop/`

- `error-context.md`

## `tests\test-results\uiux-compatibility-Visual--fd9a9-ark-theme-visual-regression-chromium-desktop/`

- `error-context.md`

## `tests\utils/`

- `api-client.ts`
- `assertion-helpers.ts`
- `wait-helpers.ts`

## `tests\wcag-reports/`

- `wcag-contrast-data-2026-08-13T08-01-36-586Z.json`
- `wcag-contrast-data-2026-08-13T09-41-59-354Z.json`
- `wcag-contrast-data-2026-08-13T11-15-24-084Z.json`
- `wcag-contrast-report-2026-08-13T08-01-36-586Z.md`
- `wcag-contrast-report-2026-08-13T09-41-59-354Z.md`
- `wcag-contrast-report-2026-08-13T11-15-24-084Z.md`

## `training/`

- `__init__.py`
- `distributed_trainer.py`

## `website/`

子目录：`docs`

- `package-lock.json`
- `package.json`

## `website\docs/`

子目录：`guide`、`public`

- `index.md`

## `website\docs\guide/`

- `api.md`
- `architecture.md`
- `faq.md`
- `install.md`
- `models.md`
- `quickstart.md`
- `security.md`
- `usage.md`
- `vram.md`
- `workflow.md`

## `website\docs\public/`

<!-- AUTO-SYNC 2026-08-27 15:16 : +2 ~8 -0 -->
