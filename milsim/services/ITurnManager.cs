namespace MilSim.Services;

/// <summary>
/// Manages WEGO turn phases: Planning → Execution → Review
/// </summary>
public interface ITurnManager
{
    int CurrentTurn { get; }
    TurnPhase CurrentPhase { get; }
    TimeSpan SimulatedTime { get; }

    /// <summary>Start the planning phase — all players can issue orders.</summary>
    Task BeginPlanningPhase(CancellationToken ct);

    /// <summary>Submit orders for a player/role. Can be called multiple times during planning.</summary>
    Task SubmitOrders(string playerId, IReadOnlyList<Order> orders, CancellationToken ct);

    /// <summary>Lock orders and begin simultaneous execution.</summary>
    Task BeginExecutionPhase(CancellationToken ct);

    /// <summary>Advance execution by one tick. Returns events that occurred.</summary>
    Task<IReadOnlyList<GameEvent>> ExecuteTick(CancellationToken ct);

    /// <summary>Enter review phase — players can inspect results.</summary>
    Task BeginReviewPhase(CancellationToken ct);

    /// <summary>Advance to next turn.</summary>
    Task AdvanceTurn(CancellationToken ct);

    event Action<TurnPhase> OnPhaseChanged;
    event Action<int> OnTurnAdvanced;
    event Action<GameEvent> OnGameEvent;
}

public enum TurnPhase
{
    Planning,
    Execution,
    Review
}

public record Order(
    string UnitId,
    OrderType Type,
    Dictionary<string, object> Parameters
);

public enum OrderType
{
    Move,
    Attack,
    Defend,
    Overwatch,
    CallForFire,
    Smoke,
    Resupply,
    Medevac,
    Mount,
    Dismount,
    Dig_In,
    Withdraw,
    ChangeStance,
    ChangeFormation
}

public record GameEvent(
    int Turn,
    int Tick,
    string EventType,
    string Description,
    string[] InvolvedUnitIds,
    Dictionary<string, object> Data
);
