import datetime
import json
import logging
import os
import time
from wrapt_timeout_decorator import *
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("desktopenv.experiment")


# Set OSWORLD_DISABLE_RECORDING=1 to skip start/end_recording and avoid writing
# recording.mp4. Each mp4 is ~2 MB and only useful for human replay; trajectories
# and screenshots are saved independently. Disabling speeds up shutdown a bit
# and saves ~600 MB per 309-task eval.
_RECORDING_DISABLED = os.environ.get("OSWORLD_DISABLE_RECORDING", "0") == "1"


def _maybe_start_recording(env):
    if _RECORDING_DISABLED:
        return
    try:
        env.controller.start_recording()
    except Exception as e:
        logger.warning(f"start_recording failed: {e}")


def _maybe_end_recording(env, path: str):
    if _RECORDING_DISABLED:
        return
    try:
        env.controller.end_recording(path)
    except Exception as e:
        logger.warning(f"end_recording failed: {e}")


def run_single_example_generate_next_obs_images(
    env,
    example: dict,
    prefix_actions: List[str],
    candidate_actions: List[str],
    next_obs_path: str,
    sleep_after_execution: float = 0.0,
    reset_wait_seconds: float = 60.0,
    rerun: bool = False,
) -> bool:
    """
    Execute ONE job:
      1) env.reset(task_config=example)
      2) wait reset_wait_seconds
      3) replay prefix_actions to reach step-k state
      4) execute candidate_actions (the uniq action list for step k)
      5) save obs["screenshot"] to next_obs_path (png bytes)

    Returns:
      True if saved successfully, else False
    """
    # ---- small local helpers (kept inside, so file still "one function" style) ----
    def _is_special(a: str) -> bool:
        x = (a or "").strip().upper()
        return x in {"WAIT", "DONE", "FAIL"}

    def _execute_actions(actions: List[str]) -> Optional[Dict[str, Any]]:
        obs = None
        done = False

        for a in actions or []:
            a = (a or "").strip()
            if not a:
                continue

            up = a.upper()
            if up == "WAIT":
                time.sleep(max(0.0, float(sleep_after_execution)))
                try:
                    obs = env._get_obs()
                except Exception:
                    pass
                continue

            if up in {"DONE", "FAIL"}:
                done = True
                break

            try:
                obs, _reward, done, _info = env.step(a, sleep_after_execution)
            except Exception as e:
                logger.error(f"env.step failed on action={a}: {e}", exc_info=True)
                return None

            if done:
                break

        return obs

    # ---- main ----
    if not isinstance(next_obs_path, str) or not next_obs_path:
        return False

    os.makedirs(os.path.dirname(next_obs_path) or ".", exist_ok=True)
    if (not rerun) and os.path.exists(next_obs_path):
        return True

    # reset
    try:
        env.reset(task_config=example)
    except Exception as e:
        logger.error(f"env.reset failed: {e}", exc_info=True)
        return False

    time.sleep(max(0.0, float(reset_wait_seconds)))

    # replay prefix
    if prefix_actions:
        obs_after_prefix = _execute_actions(prefix_actions)
        if obs_after_prefix is None:
            return False

    # execute candidate
    obs_after_candidate = _execute_actions(candidate_actions or [])
    if obs_after_candidate is None:
        # still try get current obs
        try:
            obs_after_candidate = env._get_obs()
        except Exception:
            obs_after_candidate = None

    # save screenshot
    try:
        if isinstance(obs_after_candidate, dict) and "screenshot" in obs_after_candidate:
            with open(next_obs_path, "wb") as f:
                f.write(obs_after_candidate["screenshot"])
            logger.info(f"[SAVE] {next_obs_path}")
            return True
        else:
            logger.warning(f"[WARN] No screenshot in obs for next_obs_path={next_obs_path}")
            return False
    except Exception as e:
        logger.error(f"Failed saving next_obs screenshot: {e}", exc_info=True)
        return False

def run_single_example(agent, env, example, max_steps, instruction, args, example_result_dir, scores):
    runtime_logger = setup_logger(example, example_result_dir)
    agent.reset(runtime_logger)

    env.reset(task_config=example)
    time.sleep(60)  # Wait for the environment to be ready
    obs = env._get_obs()  # Get the initial observation

    # Save initial screenshot as step_0.png (aligned)
    with open(os.path.join(example_result_dir, "step_0.png"), "wb") as _f:
        _f.write(obs["screenshot"])

    done = False
    step_idx = 0
    _maybe_start_recording(env)
    while not done and step_idx < max_steps:
        response, actions, info_dict = agent.predict(instruction, obs)

        logger.info(f"Got Action: {actions}")
        # Break if no actions
        if not actions or len(actions) == 0 or actions[0] == "" or str(actions[0]).lower().startswith("error"):
            break

        step_executed = False
        for action in actions:
            action_timestamp = datetime.datetime.now().strftime("%Y%m%d@%H%M%S")
            logger.info("Step %d: %s", step_idx + 1, action)

            obs, reward, done, info = env.step(action, args.sleep_after_execution)
            step_executed = True

            logger.info(f"Action {action} executed, reward: {reward}, done: {done}")

            # Save screenshot for this step as step_{k}.png (aligned)
            with open(os.path.join(example_result_dir, f"step_{step_idx + 1}.png"), "wb") as _f:
                _f.write(obs["screenshot"])

            # Append trajectory jsonl (aligned)
            with open(os.path.join(example_result_dir, "traj.jsonl"), "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "step_num": step_idx + 1,
                            "action": action,
                            "natural_language_action": (info_dict or {}).get("action"),
                            "action_timestamp": action_timestamp,
                            "response": response,
                            "reward": reward,
                            "done": done,
                            "info": info,
                            "screenshot_file": f"step_{step_idx + 1}.png",
                        },
                        ensure_ascii=False,
                    )
                )
                f.write("\n")

            if done:
                logger.info("The episode is done.")
                break

        if step_executed:
            agent.record_step_outcome(obs)

        step_idx += 1

    time.sleep(20)  # Wait for the environment to settle
    result = env.evaluate()
    agent.summarize(result)

    logger.info("Result: %.2f", result)
    scores.append(result)

    with open(os.path.join(example_result_dir, "result.txt"), "w", encoding="utf-8") as f:
        f.write(f"{result}\n")

    _maybe_end_recording(env, os.path.join(example_result_dir, "recording.mp4"))
def setup_logger(example, example_result_dir):
    runtime_logger = logging.getLogger(f"desktopenv.example.{example['id']}")
    runtime_logger.setLevel(logging.DEBUG)
    runtime_logger.addHandler(logging.FileHandler(os.path.join(example_result_dir, "runtime.log")))
    return runtime_logger

def run_single_example_human(env, example, max_steps, instruction, args, example_result_dir, scores):
    runtime_logger = setup_logger(example, example_result_dir)
    env.reset(task_config=example)
    time.sleep(60) # Wait for the environment to be ready
    obs = env._get_obs() # Get the initial observation
    
    # Save initial screenshot
    with open(os.path.join(example_result_dir, "initial_state.png"), "wb") as _f:
        _f.write(obs['screenshot'])
    
    # Save trajectory information
    with open(os.path.join(example_result_dir, "traj.jsonl"), "a") as f:
        f.write(json.dumps({
            "instruction": instruction,
            "initial_state": "initial_state.png"
        }))
        f.write("\n")
    
    # Evaluate the result
    result = env.evaluate()
    logger.info("Result: %.2f", result)
    scores.append(result)
    with open(os.path.join(example_result_dir, "result.txt"), "w", encoding="utf-8") as f:
        f.write(f"{result}\n")



def run_single_example_agi(agent, env, example, max_steps, instruction, args, example_result_dir, scores):
    runtime_logger = setup_logger(example, example_result_dir)
    agent.reset(runtime_logger)
    env.reset(task_config=example)
    time.sleep(60) # Wait for the environment to be ready
    obs = env._get_obs() # Get the initial observation
    done = False
    step_idx = 0
    _maybe_start_recording(env)
    while not done and step_idx < max_steps:
        response, actions = agent.predict(
            instruction,
            obs
        )

        done = not response.get('state_correct', False)

        for action in actions:
            # Capture the timestamp before executing the action
            action_timestamp = datetime.datetime.now().strftime("%Y%m%d@%H%M%S")
            logger.info("Step %d: %s", step_idx + 1, action)
            obs, reward, done, info, step_info = agent.step(action)

            if not done:
                if not response.get('state_correct', False):
                    done = True

            logger.info("Reward: %.2f", reward)
            logger.info("Done: %s", done)
            # Save screenshot and trajectory information
            with open(os.path.join(example_result_dir, f"step_{step_idx + 1}_{action_timestamp}.png"),
                      "wb") as _f:
                _f.write(obs['screenshot'])

            # Remove pending checks if they exist which will cause issues with json serialization
            if action.get('pending_checks', None):
                del action['pending_checks']

            with open(os.path.join(example_result_dir, "traj.jsonl"), "a") as f:
                f.write(json.dumps({
                    "step_num": step_idx + 1,
                    "action_timestamp": action_timestamp,
                    "action": action,
                    "reward": reward,
                    "done": done,
                    "info": info,
                    "screenshot_file": f"step_{step_idx + 1}_{action_timestamp}.png"
                }))
                f.write("\n")
            if done:
                logger.info("The episode is done.")
                break
        step_idx += 1
    result = env.evaluate()
    logger.info("Result: %.2f", result)
    scores.append(result)
    with open(os.path.join(example_result_dir, "result.txt"), "w", encoding="utf-8") as f:
        f.write(f"{result}\n")
    _maybe_end_recording(env, os.path.join(example_result_dir, "recording.mp4"))
def run_single_example_openaicua(agent, env, example, max_steps, instruction, args, example_result_dir, scores):
    runtime_logger = setup_logger(example, example_result_dir)
    agent.reset(runtime_logger)
    env.reset(task_config=example)
    time.sleep(60) # Wait for the environment to be ready
    obs = env._get_obs() # Get the initial observation
    done = False
    step_idx = 0
    _maybe_start_recording(env)
    while not done and step_idx < max_steps:
        response, actions = agent.predict(
            instruction,
            obs
        )

        done = not response.get('state_correct', False)

        for action in actions:
            # Capture the timestamp before executing the action
            action_timestamp = datetime.datetime.now().strftime("%Y%m%d@%H%M%S")
            logger.info("Step %d: %s", step_idx + 1, action)
            obs, reward, done, info, step_info = agent.step(action)

            if not done:
                if not response.get('state_correct', False):
                    done = True

            logger.info("Reward: %.2f", reward)
            logger.info("Done: %s", done)
            # Save screenshot and trajectory information
            with open(os.path.join(example_result_dir, f"step_{step_idx + 1}_{action_timestamp}.png"),
                      "wb") as _f:
                _f.write(obs['screenshot'])

            # Remove pending checks if they exist which will cause issues with json serialization
            if action.get('pending_checks', None):
                del action['pending_checks']

            with open(os.path.join(example_result_dir, "traj.jsonl"), "a") as f:
                f.write(json.dumps({
                    "step_num": step_idx + 1,
                    "action_timestamp": action_timestamp,
                    "action": action,
                    "reward": reward,
                    "done": done,
                    "info": info,
                    "screenshot_file": f"step_{step_idx + 1}_{action_timestamp}.png"
                }))
                f.write("\n")
            if done:
                logger.info("The episode is done.")
                break
        step_idx += 1
    result = env.evaluate()
    logger.info("Result: %.2f", result)
    scores.append(result)
    with open(os.path.join(example_result_dir, "result.txt"), "w", encoding="utf-8") as f:
        f.write(f"{result}\n")
    _maybe_end_recording(env, os.path.join(example_result_dir, "recording.mp4"))
def run_single_example_opencua(agent, env, example, max_steps, instruction, args, example_result_dir, scores):
    runtime_logger = setup_logger(example, example_result_dir)
    agent.reset(runtime_logger)
    env.reset(task_config=example)
    time.sleep(60) # Wait for the environment to be ready
    obs = env._get_obs() # Get the initial observation

    with open(os.path.join(example_result_dir, f"step_0.png"),
                      "wb") as _f:
                _f.write(obs['screenshot'])
    
    done = False
    step_idx = 0
    _maybe_start_recording(env)
    while not done and step_idx < max_steps:
        response, actions, info_dict = agent.predict(instruction, obs)

        logger.info(f"Got Action: {actions}")
        # Breack if no actions
        if not actions or len(actions)==0 or actions[0]=="" or actions[0].lower().startswith("error"): 
            break
        
        step_executed = False
        for action in actions:
            # Capture the timestamp before executing the action
            action_timestamp = datetime.datetime.now().strftime("%Y%m%d@%H%M%S")
            logger.info("Step %d: %s", step_idx + 1, action)
            
            obs, reward, done, info = env.step(action, args.sleep_after_execution)
            step_executed = True

            logger.info(f"Action {action} executed, reward: {reward}, done: {done}")
            # Save screenshot and trajectory information
            with open(os.path.join(example_result_dir, f"step_{step_idx + 1}.png"),
                      "wb") as _f:
                _f.write(obs['screenshot'])

            with open(os.path.join(example_result_dir, "traj.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "step_num": step_idx + 1,
                    "action": action,
                    "natural_language_action": info_dict.get("action"),
                    "action_timestamp": action_timestamp,
                    "response": response,
                    "reward": reward,
                    "done": done,
                    "info": info,
                    "screenshot_file": f"step_{step_idx + 1}_{action_timestamp}.png"
                }, ensure_ascii=False))
                f.write("\n")
            if done:
                logger.info("The episode is done.")
                break
        
        if step_executed:
            agent.record_step_outcome(obs)
        
        step_idx += 1

    time.sleep(20) # Wait for the environment to settle
    result = env.evaluate()
    agent.summarize(result)
    logger.info("Result: %.2f", result)
    scores.append(result)
    with open(os.path.join(example_result_dir, "result.txt"), "w", encoding="utf-8") as f:
        f.write(f"{result}\n")
    _maybe_end_recording(env, os.path.join(example_result_dir, "recording.mp4"))
def run_single_example_autoglm(agent, env, example, max_steps, instruction, args, example_result_dir, scores):
    runtime_logger = setup_logger(example, example_result_dir)
    try:
        agent.reset(runtime_logger)
    except Exception as e:
        agent.reset()

    env.reset(task_config=example)
    
    time.sleep(60) # Wait for the environment to be ready
    obs = env._get_obs() # Get the initial observation
    done = False
    step_idx = 0
    _maybe_start_recording(env)
    while not done and step_idx < max_steps:
        response, actions = agent.predict(
            instruction,
            obs
        )
        for action in actions:
            # Capture the timestamp before executing the action
            action_timestamp = datetime.datetime.now().strftime("%Y%m%d@%H%M%S")
            logger.info("Step %d: %s", step_idx + 1, action)
            obs, reward, done, info = env.step(action, args.sleep_after_execution)

            logger.info("Reward: %.2f", reward)
            logger.info("Done: %s", done)
            # Save screenshot and trajectory information
            with open(os.path.join(example_result_dir, f"step_{step_idx + 1}_{action_timestamp}.png"),
                      "wb") as _f:
                _f.write(obs['screenshot'])
            with open(os.path.join(example_result_dir, "traj.jsonl"), "a") as f:
                f.write(json.dumps({
                    "step_num": step_idx + 1,
                    "action_timestamp": action_timestamp,
                    "action": action,
                    "response": response,
                    "reward": reward,
                    "done": done,
                    "info": info,
                    "screenshot_file": f"step_{step_idx + 1}_{action_timestamp}.png"
                }))
                f.write("\n")
                
            if done:
                logger.info("The episode is done.")
                break
        
        # Invalid Action
        if not actions:
            obs = env._get_obs() # update observation
            
        step_idx += 1
    
    if not done: # not completed the task yet
        env.action_history.append('FAIL')
    
    result = env.evaluate()
    logger.info("Result: %.2f", result)
    scores.append(result)
    with open(os.path.join(example_result_dir, "result.txt"), "w", encoding="utf-8") as f:
        f.write(f"{result}\n")
    _maybe_end_recording(env, os.path.join(example_result_dir, "recording.mp4"))
def run_single_example_mano(agent, env, example, max_steps, instruction, args, example_result_dir, scores):
    runtime_logger = setup_logger(example, example_result_dir)
    agent.reset(runtime_logger)
    env.reset(task_config=example)
    time.sleep(60) # Wait for the environment to be ready
    obs = env._get_obs() # Get the initial observation
    done = False
    step_idx = 0
    _maybe_start_recording(env)
    with open(os.path.join(example_result_dir, f"step_0.png"),
      "wb") as _f:
        _f.write(obs['screenshot'])
    while not done and step_idx < max_steps:
        response, actions = agent.predict(
            instruction,
            obs
        )
        if len(actions) > 1:
            if (("pyautogui.hotkey('shift')" in actions[0] or "pyautogui.hotkey('ctrl')" in actions[0]) 
                and "pyautogui.click" in actions[1]):
                hotkey_type = 'shift' if "shift" in actions[0] else 'ctrl'
                action = f"pyautogui.keyDown('{hotkey_type}')\n{actions[1]}\npyautogui.keyUp('{hotkey_type}')"
                actions = [action]  
                
        for action in actions:
            # Capture the timestamp before executing the action
            action_timestamp = datetime.datetime.now().strftime("%Y%m%d@%H%M%S")
            logger.info("Step %d: %s", step_idx + 1, action)
            obs, reward, done, info = env.step(action, args.sleep_after_execution)

            logger.info("Reward: %.2f", reward)
            logger.info("Done: %s", done)
            # Save screenshot and trajectory information
            with open(os.path.join(example_result_dir, f"step_{step_idx + 1}_{action_timestamp}.png"),
                      "wb") as _f:
                _f.write(obs['screenshot'])
            with open(os.path.join(example_result_dir, "traj.jsonl"), "a") as f:
                f.write(json.dumps({
                    "step_num": step_idx + 1,
                    "action_timestamp": action_timestamp,
                    "action": action,
                    "reward": reward,
                    "done": done,
                    "info": info,
                    "screenshot_file": f"step_{step_idx + 1}_{action_timestamp}.png",
                    "response":response
                }))
                f.write("\n")
            if done:
                logger.info("The episode is done.")
                break
        step_idx += 1
    result = env.evaluate()
    logger.info("Result: %.2f", result)
    scores.append(result)
    with open(os.path.join(example_result_dir, "result.txt"), "w", encoding="utf-8") as f:
        f.write(f"{result}\n")
    _maybe_end_recording(env, os.path.join(example_result_dir, "recording.mp4"))
def run_single_example_hybrid(agent, env, example, max_steps, instruction, args, example_result_dir, scores):
    """
    Hybrid rollout loop supporting gui / mcp actions.

    MCP actions are encoded as special strings by HybridAgentLocal:
      - "MCP:<tool_name>:<json_params>"  → env.call_mcp_tool() inside VM
      - anything else                    → env.step() (pyautogui)

    MCP server lifecycle, UNO socket, tool_list, and call_tool all delegated
    to DesktopEnv methods that mirror OSWorld-MCP/osworld/desktop_env/desktop_env.py
    verbatim. No rebuilt helpers in this file.
    """
    runtime_logger = setup_logger(example, example_result_dir)
    agent.reset(runtime_logger)

    env.reset(task_config=example)
    time.sleep(60)

    # Ensure MCP HTTP server is up inside the VM (uses double-fork from QCOW's
    # pre-installed /home/user/mcp_server/server.py).
    env._ensure_mcp_server()

    # _get_obs auto-populates tool_list / tool_name via get_mcp_tool_list +
    # instruction-keyword inference fallback (matches OSWorld-MCP's
    # action_space=='mcp' path in their _get_obs).
    obs = env._get_obs()

    with open(os.path.join(example_result_dir, "step_0.png"), "wb") as _f:
        _f.write(obs["screenshot"])

    done = False
    step_idx = 0
    _maybe_start_recording(env)
    while not done and step_idx < max_steps:
        response, actions, info_dict = agent.predict(instruction, obs)

        logger.info(f"Got Action: {actions}")
        if not actions or len(actions) == 0 or actions[0] == "" or str(actions[0]).lower().startswith("error"):
            break

        step_executed = False
        for action in actions:
            action_timestamp = datetime.datetime.now().strftime("%Y%m%d@%H%M%S")
            logger.info("Step %d: %s", step_idx + 1, action)

            action_type = info_dict.get("action_type", "gui")
            parsed_action = info_dict.get("parsed_action", {})
            exec_result = {}
            exec_ok = False

            if isinstance(action, str) and action.startswith("MCP:"):
                # MCP tool call — delegate to env.call_mcp_tool (returns raw
                # str of CallToolResult, mirrors OSWorld-MCP desktop_env.py:575).
                action_type = "mcp"
                parts = action.split(":", 2)
                tool_name = parts[1] if len(parts) > 1 else ""
                params_str = parts[2] if len(parts) > 2 else "{}"
                try:
                    params = json.loads(params_str)
                except json.JSONDecodeError:
                    params = {}

                exe_result_str = env.call_mcp_tool(tool_name, params)
                # OSWorld-MCP convention: a CallToolResult string with
                # is_error=False is the success marker (see owl_agent.py:670-677).
                # Compare lowercased on both sides — the actual repr is "False"
                # (capital F) which becomes "false" after .lower().
                exec_ok = bool(exe_result_str) and "is_error=false" in exe_result_str.lower()
                # Refresh obs (new screenshot + fresh tool_list) and stash the
                # raw tool result string for the next step's "Action Response".
                obs = env._get_obs()
                obs["exe_result"] = exe_result_str or ""
                exec_result = {"ok": exec_ok, "result": exe_result_str, "error": None if exec_ok else exe_result_str}
                reward = 0.0
                done = False
                info = {"mcp_tool": tool_name, "mcp_result": exe_result_str}

            else:
                # GUI action — go through env.step (existing pyautogui path)
                action_type = "gui"
                obs, reward, done, info = env.step(action, args.sleep_after_execution)
                # env.step calls _get_obs internally which already populates
                # tool_list. Empty exe_result for GUI matches owl_agent default.
                obs["exe_result"] = ""
                exec_ok = True
                exec_result = {"ok": True}

            step_executed = True

            # Record execution result on the agent for trajectory metadata
            if hasattr(agent, "record_exec_result"):
                agent.record_exec_result(action_type, exec_ok, exec_result)

            logger.info(f"Action [{action_type}] executed, reward: {reward}, done: {done}, ok: {exec_ok}")

            with open(os.path.join(example_result_dir, f"step_{step_idx + 1}.png"), "wb") as _f:
                _f.write(obs["screenshot"])

            with open(os.path.join(example_result_dir, "traj.jsonl"), "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "step_num": step_idx + 1,
                            "action": action,
                            "action_type": action_type,
                            "parsed_action": parsed_action,
                            "exec_ok": exec_ok,
                            "exec_result": _safe_serialize(exec_result),
                            "tools_available": info_dict.get("tools_available", False),
                            "natural_language_action": (info_dict or {}).get("action"),
                            "action_timestamp": action_timestamp,
                            "response": response,
                            "reward": reward,
                            "done": done,
                            "info": _safe_serialize(info),
                            "screenshot_file": f"step_{step_idx + 1}.png",
                        },
                        ensure_ascii=False,
                    )
                )
                f.write("\n")

            if done:
                logger.info("The episode is done.")
                break

        if step_executed:
            agent.record_step_outcome(obs)

        step_idx += 1

    time.sleep(20)
    result = env.evaluate()
    agent.summarize(result)

    logger.info("Result: %.2f", result)
    scores.append(result)

    with open(os.path.join(example_result_dir, "result.txt"), "w", encoding="utf-8") as f:
        f.write(f"{result}\n")

    _maybe_end_recording(env, os.path.join(example_result_dir, "recording.mp4"))
def _safe_serialize(obj: Any) -> Any:
    """Make an object JSON-serializable by converting non-standard types."""
    if obj is None:
        return None
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


def run_single_example_uipath(agent, env, example, max_steps, instruction, args, example_result_dir, scores):
    runtime_logger = setup_logger(example, example_result_dir)
    try:
        agent.reset(runtime_logger)
    except Exception as e:
        agent.reset()

    env.reset(task_config=example)

    time.sleep(60) # Wait for the environment to be ready
    obs = env._get_obs() # Get the initial observation
    done = False
    step_idx = 0
    _maybe_start_recording(env)
    while not done and step_idx < max_steps:
        response, actions = agent.predict(
            instruction,
            obs,
            args,
            step_idx
        )
        for action in actions:
            # Capture the timestamp before executing the action
            action_timestamp = datetime.datetime.now().strftime("%Y%m%d@%H%M%S")
            logger.info("Step %d: %s", step_idx + 1, action)
            obs, reward, done, info = env.step(action, args.sleep_after_execution)

            logger.info("Reward: %.2f", reward)
            logger.info("Done: %s", done)
            # Save screenshot and trajectory information
            with open(os.path.join(example_result_dir, f"step_{step_idx + 1}_{action_timestamp}.png"),
                      "wb") as _f:
                _f.write(obs['screenshot'])
            with open(os.path.join(example_result_dir, "traj.jsonl"), "a") as f:
                f.write(json.dumps({
                    "step_num": step_idx + 1,
                    "action_timestamp": action_timestamp,
                    "action": action,
                    "response": response,
                    "reward": reward,
                    "done": done,
                    "info": info,
                    "screenshot_file": f"step_{step_idx + 1}_{action_timestamp}.png"
                }))
                f.write("\n")
            if done:
                logger.info("The episode is done.")
                break
        step_idx += 1
    result = env.evaluate()
    logger.info("Result: %.2f", result)
    scores.append(result)
    with open(os.path.join(example_result_dir, "result.txt"), "w", encoding="utf-8") as f:
        f.write(f"{result}\n")
    _maybe_end_recording(env, os.path.join(example_result_dir, "recording.mp4"))