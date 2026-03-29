namespace MilSim.Services;

/// <summary>
/// LLM-powered AI service for dynamic content generation.
/// Uses Claude API for OPFOR decisions, scenario generation, comms, and AAR.
/// </summary>
public interface IMilSimAIService
{
    // ---- OPFOR Commander AI ----

    /// <summary>
    /// Get OPFOR orders for the current turn based on game state and doctrine.
    /// The LLM acts as an enemy commander following specific military doctrine.
    /// </summary>
    Task<OPFORDecision> GetOPFORDecision(
        GameStateContext gameState, string doctrineName, CancellationToken ct);

    // ---- Dynamic Scenario Generation ----

    /// <summary>
    /// Generate a complete scenario from a natural language description.
    /// Returns structured YAML-parseable scenario definition.
    /// </summary>
    Task<GeneratedScenario> GenerateScenario(
        ScenarioRequest request, CancellationToken ct);

    // ---- Dynamic MSEL Injects ----

    /// <summary>
    /// Evaluate current game state and optionally generate a contextual MSEL inject.
    /// Returns null if no inject is appropriate this turn.
    /// </summary>
    Task<MSELInject?> EvaluateAndInject(
        GameStateContext gameState, PlayerProfile playerProfile, CancellationToken ct);

    // ---- Communications Generation ----

    /// <summary>Generate a realistic military radio communication for a game event.</summary>
    Task<string> GenerateRadioComm(
        GameEvent gameEvent, string unitCallsign, string commandCallsign, CancellationToken ct);

    /// <summary>Generate a formatted SITREP for a unit.</summary>
    Task<string> GenerateSITREP(
        string unitId, GameStateContext gameState, CancellationToken ct);

    /// <summary>Generate a 5-paragraph OPORD.</summary>
    Task<string> GenerateOPORD(
        ScenarioRequest scenario, GameStateContext gameState, CancellationToken ct);

    // ---- After Action Review ----

    /// <summary>Generate a narrative AAR from replay data.</summary>
    Task<AARNarrative> GenerateAAR(
        GameReplayData replayData, CancellationToken ct);

    /// <summary>Analyze a specific decision point in the game.</summary>
    Task<DecisionAnalysis> AnalyzeDecision(
        int turn, string description, GameReplayData replayData, CancellationToken ct);

    // ---- Adaptive Training ----

    /// <summary>Assess player performance and suggest next scenario.</summary>
    Task<TrainingRecommendation> AssessAndRecommend(
        PlayerProfile profile, GameReplayData lastGame, CancellationToken ct);

    // ---- Civilian / ROE ----

    /// <summary>Generate civilian behavior and ROE dilemmas.</summary>
    Task<CivilianEvent?> GenerateCivilianEvent(
        GameStateContext gameState, CancellationToken ct);
}

// ---- Supporting Types ----

public record GameStateContext(
    string ScenarioName,
    string Scale,
    int CurrentTurn,
    int MaxTurns,
    string TimeOfDay,
    string Weather,
    IReadOnlyList<UnitState> FriendlyUnits,
    IReadOnlyList<IntelContact> KnownEnemyContacts,
    IReadOnlyList<GameEvent> RecentEvents,
    string CurrentMission,
    string CommanderIntent,
    Dictionary<string, object> CustomData
);

public record UnitState(
    string UnitId,
    string UnitType,
    double LocationX,
    double LocationY,
    double HealthPercent,
    double MoralePercent,
    double AmmoPercent,
    double FuelPercent,
    string CurrentActivity,
    string Stance
);

public record OPFORDecision(
    IReadOnlyList<Order> Orders,
    string Rationale,             // why the AI made these decisions
    string Assessment,            // AI's assessment of enemy (player) intent
    string NextTurnIntent         // what the AI plans to do next
);

public record GeneratedScenario(
    string Name,
    string Description,
    string Scale,
    string Terrain,
    string Weather,
    string OPORD,                 // full 5-paragraph OPORD
    IReadOnlyList<UnitPlacement> BLUFORUnits,
    IReadOnlyList<UnitPlacement> OPFORUnits,
    IReadOnlyList<ObjectiveDefinition> Objectives,
    IReadOnlyList<MSELInject> PrePlannedMSEL,
    string InstructorNotes,
    string ScoringRubric
);

public record UnitPlacement(
    string UnitType,
    double LocationX,
    double LocationY,
    string Stance,
    string InitialOrders,
    Dictionary<string, object> Modifiers
);

public record ObjectiveDefinition(
    string Name,
    string Type,                  // Seize, Defend, Destroy, Preserve, Route
    double LocationX,
    double LocationY,
    int Points,
    int RequiredHoldTurns
);

public record MSELInject(
    int TriggerTurn,
    string Category,              // Intel, Weather, Civilian, Reinforcement, ROE, Higher_HQ
    string Description,
    string Effect,                // structured effect on game state
    string RadioMessage           // how it's communicated to the player
);

public record AARNarrative(
    string BattleSummary,
    IReadOnlyList<PhaseNarrative> PhaseByPhase,
    IReadOnlyList<string> KeyDecisionPoints,
    IReadOnlyList<string> LessonsLearned,
    string OverallAssessment,
    Dictionary<string, int> Statistics
);

public record PhaseNarrative(
    int StartTurn,
    int EndTurn,
    string PhaseName,
    string Narrative
);

public record DecisionAnalysis(
    int Turn,
    string DecisionDescription,
    string WhatHappened,
    string AlternativeOptions,
    string DoctrinalAssessment,
    string Recommendation
);

public record TrainingRecommendation(
    string SkillAssessment,
    IReadOnlyList<string> StrengthAreas,
    IReadOnlyList<string> ImprovementAreas,
    string RecommendedNextScenario,
    string RecommendedFocusAreas
);

public record CivilianEvent(
    string EventType,             // Convoy, Marketplace, Protest, Refugee, Media
    string Description,
    double LocationX,
    double LocationY,
    string ROEImplication,
    string CorrectResponse,       // for AAR scoring
    string RadioMessage
);

public record PlayerProfile(
    string PlayerId,
    string DisplayName,
    int GamesPlayed,
    IReadOnlyList<string> CompletedScenarios,
    Dictionary<string, double> SkillRatings,  // e.g. "fire_support_sync" -> 0.7
    string OverallRating                      // Novice, Competent, Proficient, Expert
);

public record GameReplayData(
    string ScenarioName,
    int TotalTurns,
    IReadOnlyList<GameEvent> AllEvents,
    IReadOnlyList<Order> AllOrders,
    Dictionary<string, object> FinalStatistics
);
