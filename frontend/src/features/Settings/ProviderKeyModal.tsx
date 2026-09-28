import { useNavigate } from "react-router-dom";
import Modal from "@/components/common/Modal";
import Button from "@/components/common/Button/Button";

export function ProviderKeyModal({
  isOpen,
  onClose,
  message,
  canManage = true,
}: {
  isOpen: boolean;
  onClose: () => void;
  message: string;
  /** False for members, who can't set up providers themselves — hides the navigate action. */
  canManage?: boolean;
}) {
  const navigate = useNavigate();

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="LLM provider setup required"
      footer={
        canManage ? (
          <>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button
              onClick={() => {
                onClose();
                navigate("/admin?tab=clients");
              }}
            >
              Go to Clients &amp; Providers
            </Button>
          </>
        ) : (
          <Button fullWidth onClick={onClose}>
            Ok
          </Button>
        )
      }
    >
      <p className="text-[13px] leading-relaxed text-[var(--text-secondary)]">
        {message}
      </p>
    </Modal>
  );
}

export default ProviderKeyModal;
