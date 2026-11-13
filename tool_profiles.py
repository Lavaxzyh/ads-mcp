"""Select the tool catalog before FastMCP builds per-tool schemas."""
PROFILES = ("full", "workflow", "schematic", "rf", "em")
WORKFLOW = {"ads_design_workflow", "ads_prepare_schematic_edit", "ads_diagnose_workspace",
            "ads_runtime_selfcheck", "ads_submit_rf_job", "ads_rf_job", "ads_review_rf"}
COMMON = {"ads_runtime_selfcheck", "ads_check_installed", "ads_design_workflow", "ads_prepare_schematic_edit",
          "ads_session_status", "ads_get_sim_status", "ads_get_results", "ads_diagnose_workspace"}
SCHEMATIC = {"ads_build_schematic", "ads_browse_components", "ads_audit_schematic",
             "ads_generate_netlist", "ads_open_design", "ads_list_instances",
             "ads_set_instance_parameters", "ads_open_workspace", "ads_close_workspace"}
RF = {"ads_analyze_loadpull", "ads_review_rf", "ads_build_loadpull_regions", "ads_run_matching_sweep",
      "ads_search_matching_network", "ads_analyze_matching_tolerance", "ads_submit_rf_job",
      "ads_rf_job", "ads_compare_sparameters", "ads_export_touchstone", "ads_plot_sparams"}
EM = {"ads_review_rf", "ads_create_layout", "ads_generate_layout", "ads_generate_symbol", "ads_add_layout_ports",
      "ads_create_substrate", "ads_manage_gui_session", "ads_list_gui_sessions",
      "ads_list_windows", "ads_arrange_windows", "ads_set_mesh_visibility", "ads_release_optimizer",
      "ads_open_design", "ads_run_simulation", "ads_compare_sparameters", "ads_define_cell_parameters",
      "ads_open_workspace", "ads_close_workspace", "ads_open_in_gui", "ads_export_touchstone",
      "ads_run_momentum", "ads_configure_momentum_setup", "ads_inspect_momentum_setup",
      "ads_configure_momentum_mesh", "ads_configure_momentum_options", "ads_create_em_testbench",
      "ads_create_em_optimization", "ads_create_em_candidate", "ads_configure_em_model",
      "ads_configure_em_model_storage", "ads_inspect_em_model", "ads_configure_em_parameters"}
CATALOGS = {"workflow": WORKFLOW, "schematic": COMMON | SCHEMATIC, "rf": COMMON | RF,
            "em": COMMON | EM}

def omit_repeated_titles(schema, name=None):
    """Remove display titles that repeat a property, definition or generated model name."""
    if name and schema.get("title") in (name, name.replace("_", " ").title()):
        del schema["title"]
    for group in ("properties", "$defs"):
        for child_name, child in schema.get(group, {}).items():
            omit_repeated_titles(child, child_name)
    for key in ("items", "additionalProperties"):
        child = schema.get(key)
        if isinstance(child, dict):
            omit_repeated_titles(child)
    for group in ("anyOf", "oneOf", "allOf", "prefixItems"):
        for child in schema.get(group, ()):
            omit_repeated_titles(child)


class ProfileTools:
    def __init__(self, server, profile):
        if profile not in PROFILES:
            raise ValueError("ADS_MCP_PROFILE must be one of " + ", ".join(PROFILES))
        self.server = server
        self.profile = profile

    def tool(self):
        def register(function):
            if self.profile == "full" or function.__name__ in CATALOGS[self.profile]:
                self.server.add_tool(function)
                tool = self.server._tool_manager.get_tool(function.__name__)
                omit_repeated_titles(tool.parameters, function.__name__ + "Arguments")
            return function
        return register
