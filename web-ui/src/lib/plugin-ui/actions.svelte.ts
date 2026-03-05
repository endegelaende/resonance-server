import { api } from "$lib/api";

class PluginActions {
  async dispatch(
    pluginId: string,
    action: string,
    params: Record<string, any> = {},
  ): Promise<any> {
    return api.dispatchPluginAction(pluginId, action, params);
  }
}

export const pluginActions = new PluginActions();
