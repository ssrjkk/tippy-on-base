// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Minimal ERC20 surface used by the vault (USDC on Base).
interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @title TipBotVault
/// @notice On-chain treasury for Tippy: users send USDC directly to this
///         contract, the bot (relayer) distributes payouts under a daily cap,
///         and the owner (multisig) holds full control. Anyone can verify
///         solvency on-chain: totalReserves() vs. the bot's liabilities.
contract TipBotVault {
    error OnlyOwner();
    error OnlyOwnerOrRelayer();
    error DailyLimitExceeded(uint256 spent, uint256 limit, uint256 requested);
    error MismatchedArrays();
    error EmptyDistribution();
    error Reentrant();
    error TransferFailed();
    error NotPendingOwner();
    error InsufficientReserves(uint256 requested, uint256 available);
    error Paused();
    error NotPaused();
    error BatchTooLarge(uint256 size, uint256 max);

    IERC20 public immutable usdc;

    address public owner;
    address public pendingOwner;
    address public relayer;
    bool public paused;

    uint256 public dailyLimit;
    uint256 public windowStart;
    uint256 public spentInWindow;

    uint256 private _locked = 1;

    /// @dev Maximum batch size to prevent unbounded loops that could exceed
    ///      block gas limits. 100 recipients is generous for a single tx.
    uint256 public constant MAX_BATCH_SIZE = 100;

    event Distributed(address indexed recipient, uint256 amount);
    event DistributeSkipped(address indexed recipient, uint256 amount);
    event RelayerChanged(address indexed relayer);
    event LimitChanged(uint256 limit);
    event OwnershipTransferStarted(address indexed previousOwner, address indexed newOwner);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event ReserveWithdrawn(address indexed to, uint256 amount);
    event PausedToggle(bool paused);

    constructor(address usdc_, address owner_, address relayer_, uint256 dailyLimit_) {
        usdc = IERC20(usdc_);
        owner = owner_;
        relayer = relayer_;
        dailyLimit = dailyLimit_;
        windowStart = block.timestamp;
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert OnlyOwner();
        _;
    }

    modifier onlyOwnerOrRelayer() {
        if (msg.sender != owner && msg.sender != relayer) revert OnlyOwnerOrRelayer();
        _;
    }

    modifier nonReentrant() {
        if (_locked != 1) revert Reentrant();
        _locked = 2;
        _;
        _locked = 1;
    }

    modifier whenNotPaused() {
        if (paused) revert Paused();
        _;
    }

    /// @notice Total USDC backing every balance inside the bot.
    function totalReserves() external view returns (uint256) {
        return usdc.balanceOf(address(this));
    }

    /// @notice Distribute payouts. Both relayer and owner are capped by the
    ///         daily limit to limit blast radius if either key is compromised.
    ///         Recipients whose transfer fails (e.g. USDC blacklisted) are
    ///         SKIPPED and reported via DistributeSkipped — one bad address
    ///         can no longer revert the whole batch and brick the daily window.
    ///         Only successful amounts count against the limit; the cap itself
    ///         is still checked against the requested sum.
    function batchDistribute(
        address[] calldata recipients,
        uint256[] calldata amounts
    ) external onlyOwnerOrRelayer nonReentrant whenNotPaused returns (uint256 total) {
        if (recipients.length == 0) revert EmptyDistribution();
        if (recipients.length != amounts.length) revert MismatchedArrays();
        if (recipients.length > MAX_BATCH_SIZE) revert BatchTooLarge(recipients.length, MAX_BATCH_SIZE);

        uint256 requested = 0;
        for (uint256 i = 0; i < recipients.length; i++) {
            requested += amounts[i];
        }
        // A batch that obviously cannot be covered by the reserves is an
        // operator error — revert loudly up front. Per-recipient runtime
        // failures (blacklists etc.) are still skipped below.
        if (requested > usdc.balanceOf(address(this))) {
            revert InsufficientReserves(requested, usdc.balanceOf(address(this)));
        }

        // Both relayer and owner are subject to the daily limit.
        _rollWindow();
        uint256 next = spentInWindow + requested;
        if (next > dailyLimit) {
            revert DailyLimitExceeded(spentInWindow, dailyLimit, requested);
        }

        for (uint256 i = 0; i < recipients.length; i++) {
            if (amounts[i] == 0) continue;
            // Low-level call: real USDC reverts on blacklisted recipients and
            // a plain bool-check would revert the whole batch with them.
            (bool ok, bytes memory ret) = address(usdc).call(
                abi.encodeCall(IERC20.transfer, (recipients[i], amounts[i]))
            );
            if (!ok || (ret.length != 0 && !abi.decode(ret, (bool)))) {
                emit DistributeSkipped(recipients[i], amounts[i]);
                continue;
            }
            total += amounts[i];
            emit Distributed(recipients[i], amounts[i]);
        }

        if (total > 0) {
            // Same tx as the cap check above, so the window cannot have
            // rolled in between — only successful payouts consume budget.
            spentInWindow += total;
        }
    }

    /// @notice Withdraw excess reserves (owner only, e.g. when winding down).
    ///         SECURITY: subject to the same daily cap as relayer distributions
    ///         to limit blast radius if owner key is compromised. Transfer to
    ///         multisig/timelock before mainnet deploy.
    function withdrawReserve(address to, uint256 amount) external onlyOwner nonReentrant whenNotPaused {
        if (to == address(0)) revert OnlyOwner();
        _rollWindow();
        uint256 next = spentInWindow + amount;
        if (next > dailyLimit) {
            revert DailyLimitExceeded(spentInWindow, dailyLimit, amount);
        }
        bool ok = usdc.transfer(to, amount);
        if (!ok) revert TransferFailed();
        spentInWindow += amount;
        emit ReserveWithdrawn(to, amount);
    }

    function setRelayer(address relayer_) external onlyOwner {
        relayer = relayer_;
        emit RelayerChanged(relayer_);
    }

    function setDailyLimit(uint256 limit_) external onlyOwner {
        dailyLimit = limit_;
        emit LimitChanged(limit_);
    }

    /// @notice Emergency pause: halt distributions and withdrawals. Owner-only.
    function pause() external onlyOwner {
        paused = true;
        emit PausedToggle(true);
    }

    /// @notice Resume after a pause.
    function unpause() external onlyOwner {
        paused = false;
        emit PausedToggle(false);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert OnlyOwner();
        pendingOwner = newOwner;
        emit OwnershipTransferStarted(owner, newOwner);
    }

    function cancelOwnershipTransfer() external onlyOwner {
        pendingOwner = address(0);
    }

    function acceptOwnership() external {
        if (msg.sender != pendingOwner) revert NotPendingOwner();
        emit OwnershipTransferred(owner, pendingOwner);
        owner = pendingOwner;
        pendingOwner = address(0);
    }

    /// @notice How much of the daily budget the relayer has already spent in
    ///         the current 24h window.  Rolling window prevents midnight-bypass.
    function spentTodayView() external view returns (uint256) {
        if (block.timestamp >= windowStart + 1 days) return 0;
        return spentInWindow;
    }

    function _rollWindow() private {
        if (block.timestamp >= windowStart + 1 days) {
            windowStart = block.timestamp;
            spentInWindow = 0;
        }
    }
}