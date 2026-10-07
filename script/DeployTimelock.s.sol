// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script} from "forge-std/Script.sol";
import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";

/// @notice Deploy a TimelockController for mainnet governance.
///
/// Usage:
///   forge script script/DeployTimelock.s.sol \
///     --rpc-url https://mainnet.base.org \
///     --broadcast \
///     --verify
///
/// After deployment:
///   1. Transfer ownership of OutcomeMarket, TipBotVault, VerifyingPaymaster to timelock
///   2. Deploy Gnosis Safe multisig (2-of-3 or 3-of-5)
///   3. Grant TIMELOCK_ADMIN_ROLE to multisig
///   4. Renounce deployer's admin role
///
/// @dev 24h delay gives team time to react to malicious proposals.
contract DeployTimelock is Script {
    function run() external {
        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerPrivateKey);

        // Proposers and executors should be the multisig addresses
        // For now, use deployer as placeholder — replace with Safe addresses
        address[] memory proposers = new address[](1);
        proposers[0] = deployer; // Replace with multisig

        address[] memory executors = new address[](1);
        executors[0] = deployer; // Replace with multisig

        uint256 minDelay = 2 days; // 48 hours for critical operations

        vm.startBroadcast(deployerPrivateKey);

        TimelockController timelock = new TimelockController(
            minDelay,
            proposers,
            executors,
            deployer // admin — will be transferred to multisig
        );

        vm.stopBroadcast();

        // Log the address for ownership transfers
        console.log("TimelockController deployed at:", address(timelock));
        console.log("Next steps:");
        console.log("1. Deploy Gnosis Safe multisig");
        console.log("2. Transfer contract ownerships to timelock");
        console.log("3. Grant TIMELOCK_ADMIN_ROLE to multisig");
        console.log("4. Renounce deployer admin role");
    }
}
