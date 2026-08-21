import { useStore } from "../state/store";
import { api } from "../lib/sidecar";

export function ConfirmationModal() {
  const confirmation = useStore((s) => s.confirmation);
  const clear = useStore((s) => s.clearConfirmation);

  if (!confirmation) return null;

  const answer = async (approved: boolean) => {
    try {
      await api("/confirm", {
        method: "POST",
        body: JSON.stringify({ confirm_id: confirmation.confirmId, approved }),
      });
    } catch {}
    clear();
  };

  return (
    <div className="modal-backdrop">
      <div className={`modal modal--${confirmation.risk}`}>
        <div className="modal__risk">{confirmation.risk.toUpperCase()} RISK ACTION</div>
        <div className="modal__tool">{confirmation.tool}</div>
        <pre className="modal__args">{JSON.stringify(confirmation.args, null, 2)}</pre>
        <div className="modal__buttons">
          <button className="modal__deny" onClick={() => answer(false)}>DENY</button>
          <button className="modal__allow" onClick={() => answer(true)}>ALLOW</button>
        </div>
      </div>
    </div>
  );
}
