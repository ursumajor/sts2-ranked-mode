using System;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using MegaCrit.Sts2.Core.Platform;

namespace RankedMode;

internal sealed class RankedApiException : Exception
{
    public int Status { get; }

    public RankedApiException(int status, string message) : base(message)
    {
        Status = status;
    }
}

/// <summary>HTTP client for the ranked server (see server/README.md for the API).</summary>
internal static class RankedApi
{
    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(15) };

    public static Task<JsonNode> Status() => Send(HttpMethod.Get, "/api/v1/status", null);

    public static Task<JsonNode> StartRun(string windowId) => Send(HttpMethod.Post, "/api/v1/runs/start", new JsonObject
    {
        ["window_id"] = windowId,
        ["game_version"] = RankedMod.GameVersion,
        ["mod_version"] = RankedMod.Version,
    }.ToJsonString());

    public static Task<JsonNode> Finish(string runId, string payload) =>
        Send(HttpMethod.Post, $"/api/v1/runs/{Uri.EscapeDataString(runId)}/finish", payload);

    /// <summary>Registers this Steam account with the server once and stores the returned token.</summary>
    public static async Task EnsureRegistered()
    {
        string server = RankedConfig.ServerUrl;
        if (RankedState.HasCredentialsFor(server))
        {
            return;
        }
        ulong steamId;
        string name;
        try
        {
            steamId = PlatformUtil.GetLocalPlayerId(PlatformType.Steam);
            name = PlatformUtil.GetPlayerName(PlatformType.Steam, steamId);
        }
        catch (Exception e)
        {
            throw new RankedApiException(0, "Ranked mode needs Steam to identify you: " + e.Message);
        }
        if (steamId == 0)
        {
            throw new RankedApiException(0, "Ranked mode needs Steam to identify you.");
        }
        JsonNode res;
        try
        {
            res = await Send(HttpMethod.Post, "/api/v1/register", new JsonObject
            {
                ["steam_id"] = steamId.ToString(),
                ["name"] = string.IsNullOrWhiteSpace(name) ? steamId.ToString() : name,
            }.ToJsonString(), auth: false);
        }
        catch (RankedApiException e) when (e.Status == 409)
        {
            throw new RankedApiException(409,
                "This Steam account is already registered on this ranked server from another install. " +
                "Copy ranked_mode/state.json from that install, or ask the server admin to reset your account.");
        }
        RankedState.SetCredentials(server, res["token"]!.GetValue<string>());
    }

    private static async Task<JsonNode> Send(HttpMethod method, string path, string? body, bool auth = true)
    {
        using var req = new HttpRequestMessage(method, RankedConfig.ServerUrl + path);
        if (auth && RankedState.Token != null)
        {
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", RankedState.Token);
        }
        if (body != null)
        {
            req.Content = new StringContent(body, Encoding.UTF8, "application/json");
        }
        using var resp = await Http.SendAsync(req);
        string text = await resp.Content.ReadAsStringAsync();
        if (!resp.IsSuccessStatusCode)
        {
            string message = text;
            try
            {
                message = JsonNode.Parse(text)?["detail"]?.ToString() ?? text;
            }
            catch
            {
                // non-JSON error body
            }
            throw new RankedApiException((int)resp.StatusCode, message);
        }
        return JsonNode.Parse(text) ?? new JsonObject();
    }

    public static string Describe(Exception e) => e switch
    {
        RankedApiException r => r.Message,
        HttpRequestException => $"Can't reach the ranked server at {RankedConfig.ServerUrl}.",
        TaskCanceledException => $"The ranked server at {RankedConfig.ServerUrl} timed out.",
        _ => e.Message,
    };

    public static bool IsNetworkError(Exception e) => e is HttpRequestException or TaskCanceledException;
}
