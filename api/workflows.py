"""Review, incident response, native monitoring, and isolated walkthrough routes."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from shark.guided_demo import GuidedDemo
from shark.incident_actions import TicketCapture, action_panel, capture_ticket
from shark.monthly import existing_month
from shark.monitoring import MonitoringConfig, SyncWindow, monitoring_status, save_monitoring
from shark.reviews import ReviewInput, approve_review, read_detail


class DemoAdvance(BaseModel):
    expected_step: int = Field(ge=0, le=5)


def routes(store, pipeline, watcher, monitoring, sample_text):
    router = APIRouter(prefix="/api")
    demo = GuidedDemo(store, sample_text)

    def checked(fn, *args):
        try:
            return fn(*args)
        except ValidationError as error:
            raise HTTPException(422, error.errors(include_input=False)[0]["msg"]) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/agreements/reads/{id}")
    def agreement(id: str):
        return checked(read_detail, store, id)

    @router.post("/agreements/reads/{id}/approve")
    def approve(id: str, body: ReviewInput):
        return checked(approve_review, store, id, body)

    @router.get("/vendors/{id}/monthly")
    def monthly(id: str):
        return {"case": existing_month(store, pipeline, id)}

    @router.get("/cases/{id}/history")
    def case_history(id: str):
        return {"revisions": [r for r in store.records("case_revision") if r["case_id"] == id]}

    @router.get("/alerts/{id}/action")
    def incident_action(id: int):
        return checked(action_panel, store, watcher, id)

    @router.post("/alerts/{id}/ticket")
    def record_ticket(id: int, body: TicketCapture):
        return checked(capture_ticket, store, watcher, id, body, sample_text)

    @router.get("/vendors/{id}/monitoring")
    def monitoring_config(id: str):
        return monitoring_status(store, id)

    @router.put("/vendors/{id}/monitoring")
    def configure_monitoring(id: str, body: MonitoringConfig):
        return checked(save_monitoring, store, id, body)

    @router.post("/vendors/{id}/monitoring/sync")
    def sync_monitoring(id: str, body: SyncWindow):
        return checked(monitoring.sync, id, body)

    @router.get("/demo/walkthrough")
    def walkthrough():
        return demo.state()

    @router.post("/demo/walkthrough/advance")
    def next_step(body: DemoAdvance):
        return checked(demo.advance, body.expected_step)

    @router.post("/demo/walkthrough/reset")
    def reset():
        return demo.reset()

    return router
