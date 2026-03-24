"""
MiroFish API Client
====================
HTTP client that wraps the MiroFish REST API for the trading bot.
Handles project creation, graph building, simulation, and report generation.
"""

import os
import io
import json
import time
import logging
import requests
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class MiroFishClient:
    """
    REST API client for the MiroFish prediction engine.
    
    Endpoints used:
        POST /api/graph/ontology/generate  — Upload seed data, generate ontology
        POST /api/graph/build              — Build knowledge graph
        POST /api/simulation/create        — Create simulation instance
        POST /api/simulation/prepare       — Prepare agent profiles
        POST /api/simulation/start         — Run simulation
        GET  /api/simulation/status/<id>   — Poll simulation progress
        POST /api/report/generate          — Generate prediction report
        GET  /api/report/<id>              — Fetch completed report
    """
    
    def __init__(self, base_url: str = "http://localhost:5001", timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        
    # ── Health Check ──────────────────────────────────────────────────────
    
    def is_available(self) -> bool:
        """Check if the MiroFish backend is reachable."""
        try:
            r = self.session.get(f"{self.base_url}/health", timeout=5)
            return r.status_code == 200 and r.json().get("status") == "ok"
        except Exception:
            return False
    
    # ── Graph API ─────────────────────────────────────────────────────────
    
    def generate_ontology(self, seed_text: str, simulation_requirement: str,
                          project_name: str = "MarketPrediction") -> Optional[Dict]:
        """
        Upload seed document and generate ontology.
        
        Args:
            seed_text: Market analysis text (markdown) to use as seed data. 
            simulation_requirement: Natural language description of what to predict.
            project_name: Name for the MiroFish project.
            
        Returns:
            Dict with project_id, ontology, etc. or None on failure.
        """
        try:
            # MiroFish expects multipart/form-data with a file upload
            # We create an in-memory text file from our seed document
            seed_file = io.BytesIO(seed_text.encode("utf-8"))
            
            r = self.session.post(
                f"{self.base_url}/api/graph/ontology/generate",
                data={
                    "simulation_requirement": simulation_requirement,
                    "project_name": project_name,
                    "additional_context": "Financial market analysis and prediction"
                },
                files={"files": ("market_seed.md", seed_file, "text/markdown")},
                timeout=self.timeout
            )
            r.raise_for_status()
            resp = r.json()
            
            if resp.get("success"):
                logger.info(f"[MIROFISH] Ontology generated: project_id={resp['data'].get('project_id')}")
                return resp["data"]
            else:
                logger.warning(f"[MIROFISH] Ontology generation failed: {resp.get('error')}")
                return None
                
        except requests.exceptions.ConnectionError:
            logger.warning("[MIROFISH] Service unavailable (connection refused)")
            return None
        except Exception as e:
            logger.error(f"[MIROFISH] Ontology generation error: {e}")
            return None
    
    def build_graph(self, project_id: str) -> Optional[Dict]:
        """Build knowledge graph from the generated ontology."""
        try:
            r = self.session.post(
                f"{self.base_url}/api/graph/build",
                json={"project_id": project_id},
                timeout=self.timeout * 2  # Graph building can be slow
            )
            r.raise_for_status()
            resp = r.json()
            
            if resp.get("success"):
                logger.info(f"[MIROFISH] Graph built: graph_id={resp['data'].get('graph_id')}")
                return resp["data"]
            else:
                logger.warning(f"[MIROFISH] Graph build failed: {resp.get('error')}")
                return None
                
        except Exception as e:
            logger.error(f"[MIROFISH] Graph build error: {e}")
            return None
    
    # ── Simulation API ────────────────────────────────────────────────────
    
    def create_simulation(self, project_id: str, graph_id: str = None) -> Optional[Dict]:
        """Create a new simulation instance."""
        try:
            payload = {
                "project_id": project_id,
                "enable_twitter": True,
                "enable_reddit": True,
            }
            if graph_id:
                payload["graph_id"] = graph_id
                
            r = self.session.post(
                f"{self.base_url}/api/simulation/create",
                json=payload,
                timeout=self.timeout
            )
            r.raise_for_status()
            resp = r.json()
            
            if resp.get("success"):
                sim_id = resp["data"].get("simulation_id")
                logger.info(f"[MIROFISH] Simulation created: {sim_id}")
                return resp["data"]
            else:
                logger.warning(f"[MIROFISH] Simulation creation failed: {resp.get('error')}")
                return None
                
        except Exception as e:
            logger.error(f"[MIROFISH] Simulation creation error: {e}")
            return None
    
    def prepare_simulation(self, simulation_id: str) -> Optional[Dict]:
        """Prepare simulation (generate agent profiles and config)."""
        try:
            r = self.session.post(
                f"{self.base_url}/api/simulation/prepare",
                json={"simulation_id": simulation_id},
                timeout=self.timeout * 2
            )
            r.raise_for_status()
            resp = r.json()
            
            if resp.get("success"):
                logger.info(f"[MIROFISH] Simulation prepared: {simulation_id}")
                return resp["data"]
            else:
                logger.warning(f"[MIROFISH] Simulation prepare failed: {resp.get('error')}")
                return None
                
        except Exception as e:
            logger.error(f"[MIROFISH] Simulation prepare error: {e}")
            return None
    
    def start_simulation(self, simulation_id: str, max_rounds: int = 20) -> Optional[Dict]:
        """Start running the simulation."""
        try:
            r = self.session.post(
                f"{self.base_url}/api/simulation/start",
                json={
                    "simulation_id": simulation_id,
                    "platform": "parallel",
                    "max_rounds": max_rounds,
                },
                timeout=self.timeout
            )
            r.raise_for_status()
            resp = r.json()
            
            if resp.get("success"):
                logger.info(f"[MIROFISH] Simulation started: {simulation_id}")
                return resp["data"]
            else:
                logger.warning(f"[MIROFISH] Simulation start failed: {resp.get('error')}")
                return None
                
        except Exception as e:
            logger.error(f"[MIROFISH] Simulation start error: {e}")
            return None
    
    def get_simulation_status(self, simulation_id: str) -> Optional[Dict]:
        """Get current simulation status."""
        try:
            r = self.session.get(
                f"{self.base_url}/api/simulation/status/{simulation_id}",
                timeout=30
            )
            r.raise_for_status()
            resp = r.json()
            return resp.get("data") if resp.get("success") else None
        except Exception as e:
            logger.debug(f"[MIROFISH] Status check failed: {e}")
            return None
    
    def wait_for_simulation(self, simulation_id: str, poll_interval: int = 10,
                            max_wait: int = 600) -> bool:
        """
        Block until simulation completes or times out.
        
        Returns:
            True if simulation completed, False if timed out or failed.
        """
        start = time.time()
        while time.time() - start < max_wait:
            status = self.get_simulation_status(simulation_id)
            if status is None:
                return False
            
            runner_status = status.get("runner_status", "")
            if runner_status == "completed":
                logger.info(f"[MIROFISH] Simulation {simulation_id} completed")
                return True
            elif runner_status in ("failed", "error"):
                logger.warning(f"[MIROFISH] Simulation {simulation_id} failed")
                return False
            
            time.sleep(poll_interval)
        
        logger.warning(f"[MIROFISH] Simulation {simulation_id} timed out after {max_wait}s")
        return False
    
    # ── Report API ────────────────────────────────────────────────────────
    
    def generate_report(self, simulation_id: str) -> Optional[str]:
        """
        Trigger report generation and return the task_id.
        Report generation is async — poll with get_report_status().
        """
        try:
            r = self.session.post(
                f"{self.base_url}/api/report/generate",
                json={"simulation_id": simulation_id},
                timeout=self.timeout
            )
            r.raise_for_status()
            resp = r.json()
            
            if resp.get("success"):
                data = resp["data"]
                report_id = data.get("report_id")
                task_id = data.get("task_id")
                logger.info(f"[MIROFISH] Report generation started: report_id={report_id}, task_id={task_id}")
                return report_id
            else:
                logger.warning(f"[MIROFISH] Report generation failed: {resp.get('error')}")
                return None
                
        except Exception as e:
            logger.error(f"[MIROFISH] Report generation error: {e}")
            return None
    
    def get_report(self, report_id: str) -> Optional[Dict]:
        """Fetch the completed report content."""
        try:
            r = self.session.get(
                f"{self.base_url}/api/report/{report_id}",
                timeout=self.timeout
            )
            r.raise_for_status()
            resp = r.json()
            
            if resp.get("success"):
                return resp["data"]
            return None
        except Exception as e:
            logger.error(f"[MIROFISH] Report fetch error: {e}")
            return None
    
    def wait_for_report(self, report_id: str, poll_interval: int = 5,
                        max_wait: int = 300) -> Optional[Dict]:
        """
        Poll until report is ready, then return it.
        
        Returns:
            Report dict or None on timeout/failure.
        """
        start = time.time()
        while time.time() - start < max_wait:
            report = self.get_report(report_id)
            if report and report.get("status") == "completed":
                return report
            time.sleep(poll_interval)
        
        logger.warning(f"[MIROFISH] Report {report_id} timed out after {max_wait}s")
        return None
    
    # ── Full Pipeline ─────────────────────────────────────────────────────
    
    def run_full_pipeline(self, seed_text: str, requirement: str,
                          max_rounds: int = 20,
                          project_name: str = "MarketPrediction") -> Optional[Dict]:
        """
        Run the complete MiroFish pipeline end-to-end:
        1. Generate ontology from seed data
        2. Build knowledge graph
        3. Create simulation
        4. Prepare simulation
        5. Start simulation & wait for completion
        6. Generate report & wait for it
        
        Returns:
            Report dict or None on any failure.
        """
        # 1. Ontology
        ontology = self.generate_ontology(seed_text, requirement, project_name)
        if not ontology:
            return None
        project_id = ontology["project_id"]
        
        # 2. Build graph
        graph = self.build_graph(project_id)
        if not graph:
            return None
        graph_id = graph.get("graph_id")
        
        # 3. Create simulation
        sim = self.create_simulation(project_id, graph_id)
        if not sim:
            return None
        simulation_id = sim["simulation_id"]
        
        # 4. Prepare
        prep = self.prepare_simulation(simulation_id)
        if not prep:
            return None
        
        # 5. Start & wait
        start = self.start_simulation(simulation_id, max_rounds)
        if not start:
            return None
        
        if not self.wait_for_simulation(simulation_id):
            return None
        
        # 6. Generate & fetch report
        report_id = self.generate_report(simulation_id)
        if not report_id:
            return None
        
        report = self.wait_for_report(report_id)
        return report
